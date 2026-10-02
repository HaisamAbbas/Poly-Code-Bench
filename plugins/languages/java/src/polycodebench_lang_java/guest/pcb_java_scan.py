"""Context scanner for Java sources: decide whether a construct is a real defect *in context*.

A linter reports tokens: every ``new FileInputStream`` is a ``DM_DEFAULT_ENCODING`` site whether or
not the stream is closed, and every ``while (!flag)`` is an ``await-not-in-loop`` site whether or not
it can actually miss a signal. Architecture 11.2 is explicit that "the presence of a token such as
``unwrap``, ``clone``, ``new``, or ``==`` alone is not enough to prove a violation", so this scanner
exists to give the profile the second half of the evidence.

For each rule it emits one of three verdicts:

* ``violation``          - the scanner could see that the construct is misused here;
* ``benign_in_context``  - the construct is present and correct (closed, in a loop, guarded);
* ``hint``               - the scanner cannot judge (it needs types or a runtime), and says so.

Only ``violation`` becomes a measured finding. ``benign_in_context`` and ``hint`` are recorded as
``not_applicable`` and ``needs_review`` respectively, so a token-only lint that fired at the same
site is demoted rather than counted.

This is a static scan: it strips comments, strings and character literals first, so a rule mentioned
in a comment or in a log message is never a finding. A file whose braces do not balance is reported
as not parsed, and the parser turns that into a missing scan rather than a clean one.
"""

import argparse
import json
import os
import re
import sys

SCHEMA = "pcb-java-scan-v1"

# One alternation, scanned left to right, so whichever construct starts first wins. A ``//`` inside a
# string literal is string content and a brace inside a text block is not a scope.
_LITERAL = re.compile(
    r'"""(?:.|\n)*?"""'
    r"|'(?:\\.|[^'\\\n])'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|//[^\n]*"
    r"|/\*(?:.|\n)*?\*/"
)
_RESOURCE = re.compile(
    r"\bnew\s+(?P<type>[A-Z]\w*(?:Input|Output|Reader|Writer|Stream|Connection|Statement|"
    r"PreparedStatement|ResultSet|FileInputStream|FileOutputStream|FileReader|FileWriter|"
    r"BufferedReader|BufferedWriter|Scanner|Socket|ServerSocket|ZipFile|RandomAccessFile))\b"
    r"(?P<args>\([^;]*)?"
)
_CLOSERS = re.compile(
    # try-with-resources, an explicit close, a transfer that consumes the resource, a finally that
    # closes it, or the method's own try block. These are compiled as *patterns* because the
    # previous tuple-of-strings form was compared with `in`, which never matched anything.
    r"\btry\s*\("
    r"|\.close\s*\(\s*\)"
    r"|\.transferTo\s*\("
    r"|\.transferTo\s*\(\s*OUT"
    r"|@PreDestroy"
    r"|implements\s+[^\{]*AutoCloseable"
    r"|\bfinally\b"
)
_AWAIT = re.compile(r"\bwhile\s*\(\s*!\s*(?P<flag>[A-Za-z_]\w*)")
_AWAIT_WITHIN = re.compile(r"\bawait\s*\(|\.take\s*\(|\.poll\s*\(")
_EXECUTOR = re.compile(r"\bExecutors\.new(?P<kind>Fixed|Scheduled|Cached)ThreadPool\s*\(")
_SHUTDOWN = re.compile(r"\.shutdown(?:Now)?\s*\(|AutoCloseable|@PreDestroy|\btry\s*\(")
_OPTIONAL = re.compile(r"\.get\s*\(\s*\)")
_SYSTEM_OUT = re.compile(r"System\.(?:out|err)\.print")
_INJECTION = (
    (
        "command-injection",
        re.compile(r"(?:Runtime\.getRuntime\(\)\.exec|new\s+ProcessBuilder)\s*\([^)]*\+"),
        "a process is launched with a non-literal argument list",
    ),
    (
        "sql-concatenation",
        re.compile(r"(?:executeQuery|executeUpdate|execute)\s*\([^)]*\+"),
        "a SQL statement is built by concatenation instead of a parameter",
    ),
    (
        "path-traversal",
        re.compile(r"new\s+(?:File|Path|Paths\.get)\s*\([^)]*\+"),
        "a filesystem path is built from a non-literal component",
    ),
    (
        "unsafe-deserialization",
        re.compile(r"new\s+ObjectInputStream\s*\("),
        "a stream is deserialised without an allow-list",
    ),
    (
        "weak-randomness",
        re.compile(r"new\s+Random\s*\(|Math\.random\s*\("),
        "a non-cryptographic random source is used where strength matters",
    ),
    (
        "weak-message-digest",
        re.compile(r"MessageDigest\.getInstance\s*\(\s*\"(?:MD5|SHA-?1)\""),
        "a broken message digest algorithm is requested",
    ),
)
#: A field declaration. A *method* is excluded by requiring the line to end at the semicolon, and a
#: package/import statement is excluded by requiring two identifiers (type then name) rather than
#: one, which is what made `package demo;` read as a field named `demo`.
_ASSIGN = re.compile(
    r"^\s*(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?"
    r"(?P<type>[\w.$<>\[\]]+)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=[^;]*)?;\s*$"
)
# A static field declaration *only*: the line must end at a semicolon, so a static method
# signature (which ends in `{` or `;` after its parameter list) can never match.
_STATIC_MUTABLE_FIELD = re.compile(
    r"^\s*(?:public|protected|private)?\s*static\s+(?!final\b)"
    r"(?P<type>[\w.$<>\[\], ]*\b(?:List|Map|Set|ArrayList|HashMap|HashSet|Collection|StringBuilder)"
    r"[\w.$<>\[\], ]*)\s+[A-Za-z_]\w*\s*(?:=[^;]*)?;\s*$"
)
_USELESS_ASSIGN = re.compile(r"^\s*(?P<name>[A-Za-z_]\w*)\s*=\s*(?P=name)\s*;\s*$")
_DEBUGGER = re.compile(r"\bbreakpoint\s*\(|\bassert\s+(?:false|True)\b")
_KEYWORDS = frozenset(
    {"return", "throw", "break", "continue", "package", "import", "assert", "yield", "case"}
)

#: Rules whose presence is only a defect in a specific context. A rule absent from this map is
#: emitted with ``medium`` confidence by default.
_RULE_TEXT = {
    "resource-unclosed": "an acquired resource is never closed on any path",
    "resource-closed-on-all-paths": "acquired resources are released on every path",
    "await-not-in-loop": "a condition is awaited outside the loop that re-checks it",
    "executor-never-shutdown": "an executor is created and never shut down",
    "published-mutable-state": "mutable state is published without safe-publication",
    "optional-ignored": "an Optional is created and immediately discarded",
    "implicit-optional-get": "Optional.get is called without a presence check",
    "dead-store": "a local variable is assigned and never read",
    "self-assignment": "a variable is assigned to itself",
    "system-out-in-library": "debug output is written from non-test code",
    "legacy-string-concat": "string concatenation is used where a formatter is appropriate",
    "command-injection": "a process is launched with a non-literal argument list",
    "sql-concatenation": "a SQL statement is built by concatenation instead of a parameter",
    "path-traversal": "a filesystem path is built from a non-literal component",
    "unsafe-deserialization": "a stream is deserialised without an allow-list",
    "weak-randomness": "a non-cryptographic random source is used where strength matters",
    "weak-message-digest": "a broken message digest algorithm is requested",
}


def _blank(match):
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text):
    """Blank out comments, strings and char literals, keeping every newline (line numbers)."""
    return _LITERAL.sub(_blank, text)


def _lines(text):
    """Sanitized source lines; index 0 is line 1."""
    return sanitize(text).splitlines()


def _texts(lines):
    return lines


def _block(lines, index):
    """The brace-balanced block starting at ``lines[index]``, or the rest of the file."""
    depth = 0
    opened = False
    for offset in range(index, len(lines)):
        depth += lines[offset].count("{") - lines[offset].count("}")
        if lines[offset].count("{"):
            opened = True
        if opened and depth <= 0:
            return lines[index : offset + 1], offset
    return lines[index:], len(lines) - 1


def _verdict(rule, symbol, detail=""):
    return {
        "rule": rule,
        "path": "",
        "line": 1,
        "end_line": 1,
        "column": 1,
        "verdict": "hint",
        "confidence": "low",
        "symbol": symbol,
        "message": detail or _RULE_TEXT.get(rule, rule),
    }


def scan_text(path, text):
    """Findings for one source file. Pure text in, findings out; nothing is compiled or executed."""
    raw = text.decode("utf-8", errors="replace")
    if raw.count("{") != raw.count("}"):
        return [], False
    rows = list(_lines(raw))
    findings = []
    seen = set()

    def emit(line_no, rule, verdict, symbol, detail=""):
        key = (line_no, rule)
        if key in seen:
            return
        seen.add(key)
        entry = _verdict(rule, symbol, detail)
        entry["path"] = path
        entry["line"] = line_no
        entry["end_line"] = line_no
        entry["column"] = max(1, rows[min(line_no - 1, len(rows) - 1)].find(symbol) + 1)
        entry["verdict"] = verdict
        entry["confidence"] = "high" if verdict == "violation" else "medium"
        findings.append(entry)

    # --- resources: acquired, and then closed or not ------------------------------------------
    for index, line in enumerate(rows):
        number = index + 1
        for match in _RESOURCE.finditer(line):
            tail, _ = _block(rows, index)
            body = "\n".join(tail)
            kind = match.group("type")
            guarded = _CLOSERS.search(body) is not None
            if not guarded:
                emit(number, "resource-unclosed", "violation", kind)
            else:
                emit(number, "resource-closed-on-all-paths", "benign_in_context", kind)
            # An acquisition inside a loop body leaks once per iteration even when it is closed.
            if index > 0 and re.search(r"\b(for|while)\s*\(", rows[index - 1]):
                emit(number, "resource-acquisition-in-loop", "violation", kind)

    # --- concurrency ----------------------------------------------------------------------------
    for index, line in enumerate(rows):
        match = _AWAIT.search(line)
        if match and not _AWAIT_WITHIN.search(line):
            emit(index + 1, "await-not-in-loop", "violation", match.group("flag"))
    for index, line in enumerate(rows):
        if _EXECUTOR.search(line):
            tail, _ = _block(rows, index)
            if not _SHUTDOWN.search("\n".join(tail)):
                emit(index + 1, "executor-never-shutdown", "violation", "Executors")
    for index, line in enumerate(rows):
        if _STATIC_MUTABLE_FIELD.match(line):
            emit(index + 1, "published-mutable-state", "violation", "static")

    # --- nullability ----------------------------------------------------------------------------
    for index, line in enumerate(rows):
        number = index + 1
        if _OPTIONAL.search(line) and not re.search(
            r"isPresent|orElse|ifPresent|orElseThrow", line
        ):
            emit(number, "implicit-optional-get", "violation", ".get()")
        if re.search(r"Optional\.of\s*\(\s*null\s*\)", line):
            emit(number, "implicit-optional-get", "violation", "Optional.of(null)")

        # --- structure ---------------------------------------------------------------------------
        if _SYSTEM_OUT.search(line):
            emit(number, "system-out-in-library", "violation", "System.out")
        if _DEBUGGER.search(line):
            emit(number, "dead-store", "violation", "assert")
        useless = _USELESS_ASSIGN.match(line)
        if useless:
            emit(number, "self-assignment", "violation", useless.group("name"))
        if re.search(r"new\s+String\s*\(\s*\w+\s*\.\s*getBytes\s*\(\s*\)", line):
            emit(number, "legacy-string-concat", "violation", "new String(bytes)")
        # A field that is assigned and never read again is a dead store. This is a hint, never a
        # violation: without types the scanner cannot tell a write-only field from one the rest of
        # the package reads, and guessing would penalise a correct candidate.
        field = _ASSIGN.match(line)
        if field is not None and field.group("type") not in _KEYWORDS:
            name = field.group("name")
            if name not in "\n".join(rows[index + 1 : index + 25]):
                emit(number, "dead-store", "hint", name)

    # --- security surface -----------------------------------------------------------------------
    for rule, pattern, detail in _INJECTION:
        for index, line in enumerate(rows):
            match = pattern.search(line)
            if match:
                emit(index + 1, rule, "violation", match.group(0)[:40], detail)
    return findings, True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pcb_java_scan")
    parser.add_argument("--root", default="/workspace")
    parser.add_argument("--output", required=True)
    parser.add_argument("--opportunities", default="")
    parser.add_argument("files", nargs="*")
    options = parser.parse_args(argv)
    tags = [tag for tag in options.opportunities.split(",") if tag]
    results = []
    complete = True
    for relative in options.files:
        path = relative
        if path.startswith(options.root):
            path = path[len(options.root) :].lstrip("/")
        if path.startswith("work/"):
            path = path[len("work/") :]
        try:
            with open(relative, "rb") as handle:
                data = handle.read()
        except OSError:
            complete = False
            results.append({"path": path, "parsed": False, "findings": []})
            continue
        findings, parsed = scan_text(path, data)
        if not parsed:
            complete = False
        results.append({"path": path, "parsed": parsed, "findings": findings})
    document = {
        "schema": SCHEMA,
        "complete": complete,
        "opportunities": tags,
        "files": results,
        "findings": [item for entry in results for item in entry["findings"]],
    }
    parent = os.path.dirname(options.output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(options.output, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    return 1 if document["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
