"""Offline Go module audit: ``go.mod``/``go.sum`` against the pinned advisory snapshot.

A Go toolchain pins the *compiler*. It does not pin what a module resolves to: ``go.mod`` declares
requirements and ``go.sum`` records the hashes the module graph resolved to, so together they are
the second half of the evaluator identity. This script answers two questions without a network:

1. **is the resolution pinned?** ``go.sum`` must cover every required module, otherwise two runs of
   the same source could resolve different content;
2. **does the pinned resolution contain a known advisory?** against
   ``rules/advisories/snapshot.json``.

A missing, empty or unreadable snapshot is exit 2 with a message on stderr: an audit that cannot
consult its database must never read as a clean module graph.

Usage:
    python pcb_go_mod_audit.py --module work/go.mod --sum work/go.sum \\
        --advisories /opt/pcb/rules/advisories/snapshot.json --output out/dependency.json
Exit: 0 no advisory, 1 advisory found, 2 the audit could not be performed.
"""

import json
import os
import re
import sys

SCHEMA = "pcb-go-mod-audit-v1"

_REQUIRE = re.compile(
    r"^\s*(?:require\s+)?(?P<path>[a-z0-9.\-/]+\.[a-z]{2,}[^\s]*)\s+v(?P<version>[^\s/]+)"
)
_MODULE_DIRECTIVE = re.compile(r"^module\s+(?P<path>\S+)")
_SUM = re.compile(r"^(?P<path>\S+)\s+(?P<version>\S+?)(?:/go\.mod)?\s+h1:")


def _parse_args(argv):
    options = {}
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--module", "--sum", "--advisories", "--output") and index + 1 < len(argv):
            options[flag[2:].replace("-", "_")] = argv[index + 1]
            index += 2
        else:
            raise SystemExit(2)
    for needed in ("module", "advisories", "output"):
        if needed not in options:
            raise SystemExit(2)
    return options


def _read(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8")


def _requires(text):
    """Direct and indirect requirements, with the module's own path excluded."""
    module = None
    required = []
    in_block = False
    for line in text.splitlines():
        stripped = line.split("//", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("module "):
            match = _MODULE_DIRECTIVE.match(stripped)
            module = match.group("path") if match else None
            continue
        if stripped.startswith("require ("):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        if in_block or stripped.startswith("require "):
            candidate = stripped.removeprefix("require ").strip()
            match = _REQUIRE.match(candidate)
            if match and match.group("path") != module:
                required.append((match.group("path"), match.group("version")))
    return module, sorted(set(required))


def _summed(text):
    summed = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _SUM.match(stripped)
        if match:
            summed.add((match.group("path"), match.group("version")))
    return summed


def audit(module_path, sum_path, snapshot):
    """Findings for one module graph. ``snapshot`` is the parsed advisory document."""
    module, required = _requires(_read(module_path))
    summed = _summed(_read(sum_path)) if sum_path and os.path.exists(sum_path) else set()
    advisories = snapshot.get("advisories") or {}
    findings = []
    unpinned = []
    for path, version in required:
        if (path, version) not in summed:
            unpinned.append(f"{path}@{version}")
        for key, entry in advisories.items():
            affected = entry.get("module") if isinstance(entry, dict) else None
            if affected and affected != path:
                continue
            if not affected and key.split("@", 1)[0] != path:
                continue
            fixed = entry.get("fixed_in") if isinstance(entry, dict) else None
            if fixed and _at_least(version, fixed):
                continue
            findings.append(
                {
                    "advisory": key,
                    "package": path,
                    "version": version,
                    "severity": str((entry or {}).get("severity", "medium")).lower(),
                    "fixed_in": fixed,
                }
            )
    findings.sort(key=lambda item: (item["package"], item["advisory"]))
    return {
        "schema": SCHEMA,
        "module": module,
        "snapshot_source": snapshot.get("source"),
        "snapshot_entries": len(advisories),
        "checked": len(required),
        "unpinned": unpinned,
        "findings": findings,
    }


def _at_least(version, fixed):
    """Whether a semantic version has reached ``fixed`` (Go versions are dotted, with suffixes)."""

    def parts(text):
        core = text.strip().removeprefix("v").split("-", 1)[0].split("+", 1)[0]
        return tuple(int(piece) if piece.isdigit() else 0 for piece in core.split(".")[:3])

    try:
        return parts(version) >= parts(fixed)
    except (TypeError, ValueError):
        return False


def main(argv):
    options = _parse_args(argv)
    try:
        with open(options["advisories"], "rb") as handle:
            snapshot = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        sys.stderr.write("advisory snapshot is unreadable: %s\n" % type(error).__name__)
        return 2
    if not snapshot.get("advisories"):
        sys.stderr.write(
            "advisory snapshot is empty; the dependency audit would report a clean module graph "
            "without checking anything\n"
        )
        return 2
    try:
        result = audit(options["module"], options.get("sum"), snapshot)
    except OSError as error:
        sys.stderr.write("module files are unreadable: %s\n" % type(error).__name__)
        return 2
    parent = os.path.dirname(options["output"])
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(options["output"], "w", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True, separators=(",", ":"))
    return 1 if result["findings"] or result["unpinned"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
