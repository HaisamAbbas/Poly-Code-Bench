"""Compile-check candidate files without writing bytecode (the Python "build" step).

Usage: python pcb_syntax_check.py --root DIR --output FILE PATH...
Exit status: 0 every file compiles, 1 at least one file does not, 2 unreadable input/usage.
"""

import json
import os
import sys


def _parse(args):
    root = output = None
    paths = []
    index = 0
    while index < len(args):
        if args[index] == "--root" and index + 1 < len(args):
            root = args[index + 1]
            index += 2
        elif args[index] == "--output" and index + 1 < len(args):
            output = args[index + 1]
            index += 2
        else:
            paths.append(args[index])
            index += 1
    return root, output, paths


def main(argv):
    root, output, paths = _parse(argv[1:])
    if root is None or output is None or not paths:
        return 2
    files, failed, unreadable = [], False, False
    for rel in paths:
        full = os.path.join(root, rel)
        try:
            with open(full, "rb") as handle:
                source = handle.read()
        except OSError:
            files.append({"path": rel, "ok": False, "error": "unreadable", "line": None})
            unreadable = True
            continue
        try:
            compile(source, rel, "exec", dont_inherit=True)
            files.append({"path": rel, "ok": True, "error": None, "line": None})
        except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
            failed = True
            files.append(
                {
                    "path": rel,
                    "ok": False,
                    "error": type(error).__name__,
                    "line": getattr(error, "lineno", None),
                    "message": str(getattr(error, "msg", error))[:200],
                }
            )
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump({"schema": "pcb-syntax-check-v1", "files": files}, handle, sort_keys=True)
    if unreadable:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
