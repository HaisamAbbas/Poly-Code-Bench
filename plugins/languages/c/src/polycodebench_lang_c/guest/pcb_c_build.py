"""Compile and link one C target in the guest, recording normalized diagnostics.

Usage:
    python pcb_c_build.py --out out/build.json --build-dir /workspace/build \\
        --binary /workspace/build/target [--archive] [--std c17] [--warn-error] [--warning-set strict] \\
        [--sanitizer address] [--source work/src/a.c] [--source work/tests/behaviour.c] \\
        [--object-include tests/hidden] --include work/include [--execute plain|valgrind]

Writes ``out/build.json`` (schema ``pcb-c-build-v1``) and exits:

* ``0`` the target linked;
* ``1`` the target compiled but warnings were promoted to errors by the frozen policy;
* ``2`` a compile, link or archive error;
* ``3`` incomplete - a compiler that could not be started, or a write that failed.

Every diagnostic is normalized here, in the guest, while the compiler's line-oriented output is
still the only thing parsed. The exit status is never read as a verdict: clang's own output says
whether a unit compiled, and the parser reads that rather than trusting the code.

The flag set is hashed into the document. That digest is what makes a warning attributable: two runs
of the same compiler with different flags are not the same evidence.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA = "pcb-c-build-v1"
EXIT_OK = 0
EXIT_WARNINGS_AS_ERRORS = 1
EXIT_COMPILE_ERROR = 2
EXIT_INCOMPLETE = 3

_DIAGNOSTIC = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>fatal error|error|warning|note)\s*:\s*(?P<message>.+?)"
    r"(?:\s*\[(?P<rule>[^\]]+)\])?\s*$"
)
_BARE = re.compile(r"^(?P<severity>fatal error|error|warning):\s*(?P<message>.+)$")
_NOISE = re.compile(
    r"^(?:Suppressed|Enabled|Checks|files? checked|Total|Checking|Project|Detected|Active)\b",
    re.IGNORECASE,
)

_WARNING_SETS = {
    "none": (),
    "minimal": ("-Wall",),
    "standard": ("-Wall", "-Wextra"),
    "strict": (
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-Wshadow",
        "-Wconversion",
        "-Wsign-conversion",
        "-Wcast-qual",
        "-Wmissing-prototypes",
        "-Wstrict-prototypes",
        "-Wold-style-definition",
        "-Wundef",
        "-Wwrite-strings",
        "-Wformat=2",
    ),
}
_OPTIMIZATION = {
    "runtime": ("-O1",),
    "evaluator": ("-O0",),
    "instrumented": ("-O1",),
    "performance": ("-O2", "-DNDEBUG"),
}
_SANITIZER_FLAGS = {
    "address": ("-fsanitize=address", "-fno-omit-frame-pointer"),
    "undefined": ("-fsanitize=undefined", "-fno-sanitize-recover=undefined"),
}
_RECIPES = tuple(_OPTIMIZATION)
#: Where the pinned harness and rules bundle live. Always on the include path: the hidden test groups
#: include ``pcb_ctest.h`` by name, and every binary links the same driver, so a task cannot supply a
#: header of its own under that name.
RULES = "/opt/pcb/rules"
#: Options that may appear more than once, mapped to the key they append to. Named explicitly rather
#: than derived from the flag text so a plural never becomes a singular key by accident.
_REPEATED = {"--source": "sources", "--object-include": "object_include"}


def _parse(argv):
    options = {
        "out": None,
        "build_dir": None,
        "binary": None,
        "std": "c17",
        "warning_set": "standard",
        "warn_error": False,
        "sanitizer": None,
        "recipe": "runtime",
        "include": None,
        "object_include": [],
        "sources": [],
        "execute": None,
        "run_deadline": 30.0,
        "archive": False,
    }
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag == "--warn-error":
            options["warn_error"] = True
            index += 1
        elif flag == "--archive":
            options["archive"] = True
            index += 1
        elif flag in _REPEATED:
            options[_REPEATED[flag]].append(argv[index + 1])
            index += 2
        elif flag.startswith("--") and index + 1 < len(argv):
            value = argv[index + 1]
            options[flag[2:].replace("-", "_")] = value
            index += 2
        else:
            raise SystemExit(2)
    for required in ("out", "build_dir", "binary", "include"):
        if options[required] is None:
            raise SystemExit(2)
    if not options["sources"]:
        raise SystemExit(2)
    if options["warning_set"] not in _WARNING_SETS:
        raise SystemExit(2)
    if options["sanitizer"] is not None and options["sanitizer"] not in _SANITIZER_FLAGS:
        raise SystemExit(2)
    if options["recipe"] not in _RECIPES:
        raise SystemExit(2)
    if options["execute"] is not None and options["execute"] not in ("plain", "valgrind"):
        raise SystemExit(2)
    if options["execute"] is not None and options["archive"]:
        raise SystemExit(2)
    options["run_deadline"] = float(options["run_deadline"])
    return options


def compile_flags(options):
    flags = ["-std=" + options["std"]]
    flags.extend(_OPTIMIZATION[options["recipe"]])
    flags.extend(_WARNING_SETS[options["warning_set"]])
    if options["warn_error"]:
        flags.append("-Werror")
    if options["sanitizer"] is not None:
        flags.extend(_SANITIZER_FLAGS[options["sanitizer"]])
    flags.append("-I" + options["include"])
    for extra in options["object_include"]:
        flags.append("-I" + extra)
    flags.append("-I" + RULES)
    # Line-oriented diagnostics only: carets and colour would make the output position-dependent.
    flags.extend(("-fno-color-diagnostics", "-fno-caret-diagnostics"))
    return tuple(flags)


def link_flags(options):
    flags = list(_SANITIZER_FLAGS.get(options["sanitizer"], ()))
    if options["sanitizer"] is None:
        # Full RELRO and immediate binding: the release lane links a hardened binary, and the
        # flag difference is part of what the performance image records.
        flags.append("-Wl,-z,now")
    return tuple(flags)


def parse_diagnostics(text, source):
    """Normalized diagnostics from one compilation unit's combined output."""
    found = []
    bare = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _NOISE.match(line):
            continue
        match = _DIAGNOSTIC.match(line)
        if match is not None:
            severity = match.group("severity")
            found.append(
                {
                    "path": match.group("path").strip(),
                    "line": int(match.group("line")),
                    "column": int(match.group("column")) if match.group("column") else None,
                    "severity": "high" if severity in {"error", "fatal error"} else (
                        "medium" if severity == "warning" else "low"
                    ),
                    "raw_severity": severity,
                    "message": match.group("message").strip()[:400],
                    "rule": match.group("rule"),
                    "source": source,
                }
            )
            continue
        bare_match = _BARE.match(line)
        if bare_match is not None:
            bare.append(
                {
                    "path": None,
                    "line": 0,
                    "column": None,
                    "severity": "high"
                    if bare_match.group("severity") in {"error", "fatal error"}
                    else "medium",
                    "raw_severity": bare_match.group("severity"),
                    "message": bare_match.group("message").strip()[:400],
                    "rule": None,
                    "source": source,
                }
            )
    return found, bare


def _object_for(source, build_dir):
    stem = os.path.basename(source).rsplit(".", 1)[0]
    # Two files with the same basename in different directories are common in C; the digest keeps
    # their objects apart instead of silently overwriting one with the other.
    tag = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return os.path.join(build_dir, stem + "-" + tag + ".o")


def main(argv):
    options = _parse(argv)
    flags = compile_flags(options)
    linker = link_flags(options)
    flags_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "compile": list(flags),
                "link": list(linker),
                "std": options["std"],
                "warning_set": options["warning_set"],
                "warn_error": options["warn_error"],
                "sanitizer": options["sanitizer"],
                "recipe": options["recipe"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        os.makedirs(options["build_dir"], exist_ok=True)
    except OSError as error:
        return _write(
            options,
            units=[],
            linked=False,
            flags=flags,
            linker=linker,
            flags_digest=flags_digest,
            complete=False,
            fatal="build directory is not writable: %s" % error,
        )
    units = []
    diagnostics = []
    objects = []
    for source in options["sources"]:
        command = [
            "clang",
            "-c",
            source,
            "-o",
            _object_for(source, options["build_dir"]),
        ]
        command.extend(flags)
        try:
            returned = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except (FileNotFoundError, OSError) as error:
            return _write(
                options,
                units=[],
                linked=False,
                flags=flags,
                linker=linker,
                flags_digest=flags_digest,
                complete=False,
                fatal="compiler could not be started: %s" % error,
            )
        text = returned.stdout + returned.stderr
        found, bare = parse_diagnostics(text, source)
        diagnostics.extend(found)
        diagnostics.extend(bare)
        units.append(
            {
                "source": source,
                "exit_code": returned.returncode,
                "ok": returned.returncode == 0,
                "error_count": sum(1 for d in found if d["raw_severity"] in {"error", "fatal error"}),
                "warning_count": sum(1 for d in found if d["raw_severity"] == "warning"),
            }
        )
        objects.append(_object_for(source, options["build_dir"]))
    compile_errors = [d for d in diagnostics if d["raw_severity"] in {"error", "fatal error"}]
    warnings = [d for d in diagnostics if d["raw_severity"] == "warning"]
    if compile_errors:
        return _write(
            options,
            units=units,
            diagnostics=diagnostics,
            linked=False,
            flags=flags,
            linker=linker,
            flags_digest=flags_digest,
            complete=True,
            fatal=None,
        )
    if options["warn_error"] and warnings:
        # The policy says a warning is a build failure. That is recorded here, as a build verdict,
        # and the same diagnostics still reach the analyzers as evidence.
        return _write(
            options,
            units=units,
            diagnostics=diagnostics,
            linked=False,
            flags=flags,
            linker=linker,
            flags_digest=flags_digest,
            complete=True,
            fatal=None,
            blocked_by_warnings=True,
        )
    link_command = (
        ["ar", "rcs", options["binary"]] + objects
        if options["archive"]
        else ["clang", "-o", options["binary"]] + objects + list(linker)
    )
    try:
        linked = subprocess.run(
            link_command, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace"
        )
    except (FileNotFoundError, OSError) as error:
        return _write(
            options,
            units=units,
            diagnostics=diagnostics,
            linked=False,
            flags=flags,
            linker=linker,
            flags_digest=flags_digest,
            complete=False,
            fatal="linker could not be started: %s" % error,
        )
    link_text = linked.stdout + linked.stderr
    link_diagnostics, link_bare = parse_diagnostics(link_text, "link")
    diagnostics.extend(link_diagnostics)
    diagnostics.extend(link_bare)
    if linked.returncode != 0:
        return _write(
            options,
            units=units,
            diagnostics=diagnostics,
            linked=False,
            flags=flags,
            linker=linker,
            flags_digest=flags_digest,
            complete=True,
            fatal="link failed",
        )
    run = None
    if options["execute"] is not None:
        run = _execute(options)
    return _write(
        options,
        units=units,
        diagnostics=diagnostics,
        linked=True,
        flags=flags,
        linker=linker,
        flags_digest=flags_digest,
        complete=True,
        fatal=None,
        run=run,
    )


def _execute(options):
    """Run the freshly linked binary, optionally under Valgrind, into the plan's own stdout.

    One driver rather than two steps keeps the argument vector typed: there is no shell, no ``&&``
    and no intermediate plan whose failure could be read as the lane's result.
    """
    if options["execute"] == "valgrind":
        command = [
            "valgrind",
            "--error-exitcode=42",
            "--errors-for-leak-kinds=definite",
            "--leak-check=full",
            "--track-origins=yes",
            "--num-callers=20",
            "--child-silent-after-fork=yes",
            options["binary"],
        ]
    else:
        command = [options["binary"]]
    environment = dict(os.environ)
    if options["sanitizer"] == "address":
        # AddressSanitizer must come first in the initial library list or the runtime is not loaded
        # and the "clean" result would be about nothing.
        environment["LD_PRELOAD"] = _asan_runtime()
        environment["ASAN_OPTIONS"] = "abort_on_error=0:exitcode=1:detect_leaks=1"
    elif options["sanitizer"] == "undefined":
        environment["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=0"
    try:
        returned = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=options["run_deadline"],
            env=environment,
        )
    except subprocess.TimeoutExpired as expired:
        sys.stdout.write((expired.stdout or "") if isinstance(expired.stdout, str) else "")
        sys.stdout.write((expired.stderr or "") if isinstance(expired.stderr, str) else "")
        sys.stdout.flush()
        return {"exit_code": None, "timed_out": True, "mode": options["execute"]}
    sys.stdout.write(returned.stdout)
    sys.stderr.write(returned.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    return {
        "exit_code": returned.returncode,
        "timed_out": False,
        "mode": options["execute"],
        "stdout_digest": "sha256:"
        + hashlib.sha256(returned.stdout.encode("utf-8", "replace")).hexdigest(),
    }


def _asan_runtime():
    """Absolute path of the AddressSanitizer runtime, resolved once per guest.

    clang ships the runtime next to the driver; an empty value means "let the linked binary use its
    own", which is the normal case because the flags were linked in.
    """
    try:
        completed = subprocess.run(
            ["clang", "-print-file-name=libclang_rt.asan-x86_64.so"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return ""
    path = completed.stdout.strip()
    return path if path.endswith(".so") and "/" in path else ""


def _write(
    options,
    *,
    units,
    flags,
    linker,
    flags_digest,
    complete,
    fatal,
    diagnostics=None,
    linked=False,
    blocked_by_warnings=False,
    run=None,
):
    document = {
        "schema": SCHEMA,
        "compiler": "clang",
        "std": options["std"],
        "recipe": options["recipe"],
        "warning_set": options["warning_set"],
        "warnings_as_errors": bool(options["warn_error"]),
        "sanitizer": options["sanitizer"],
        "execute": options["execute"],
        "archive": bool(options["archive"]),
        "run": run,
        "compile_flags": list(flags),
        "link_flags": list(linker),
        "flags_digest": flags_digest,
        "units": units,
        "diagnostics": diagnostics or [],
        "diagnostic_count": len(diagnostics or []),
        "error_count": sum(
            1 for d in (diagnostics or []) if d["raw_severity"] in {"error", "fatal error"}
        ),
        "warning_count": sum(1 for d in (diagnostics or []) if d["raw_severity"] == "warning"),
        "linked": bool(linked),
        "blocked_by_warnings": bool(blocked_by_warnings),
        "complete": bool(complete),
        "fatal": fatal,
        "binary": options["binary"] if linked else None,
    }
    parent = os.path.dirname(options["out"])
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(options["out"], "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    if not complete:
        return EXIT_INCOMPLETE
    if document["error_count"] or not linked:
        return EXIT_COMPILE_ERROR
    if blocked_by_warnings:
        return EXIT_WARNINGS_AS_ERRORS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))