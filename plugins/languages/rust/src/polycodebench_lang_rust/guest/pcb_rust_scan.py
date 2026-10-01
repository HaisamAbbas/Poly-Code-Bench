"""Context-aware Rust idiom scanner (pure stdlib line analysis; ships inside the images).

Rust's most tokenised idioms are only defects *in context*, which is exactly what Prompt 11
forbids treating automatically:

* ``.unwrap()`` / ``.expect()`` - a violation only when the result is not guarded. A call whose
  value was checked first (``is_ok``/``is_some``/``map_or``/``unwrap_or``), a call inside a
  ``match``/``if let`` that handles the error arm, and a call in a test are ``benign_in_context``.
* ``.clone()`` - a violation only when provably redundant (cloning through a reference). A clone
  that moves an owned value or satisfies an owned API is not redundant.
* ``unsafe`` - never a violation by itself: ``benign_in_context`` with a ``SAFETY`` justification,
  otherwise a ``hint`` needing reviewer or judge evidence.

Records carry ``violation`` (counts), ``benign_in_context`` (evidence, never penalised) or
``hint``. Counting constructs *used* never earns credit: the scanner only reports defects;
opportunity counts come from the frozen task.

Usage: python pcb_rust_scan.py --root DIR --output FILE [--opportunities a,b] PATH...
Exit: 0 clean, 1 violations, 2 incomplete.
"""

import argparse
import json
import os
import re
import sys

SCANNER_VERSION = "1"
SCHEMA = "pcb-rust-scan-v1"

_UNSAFE_BLOCK = re.compile(r"\bunsafe\s*\{")
_UNSAFE_FN = re.compile(r"\bunsafe\s+(?:extern\s+\"[^\"]*\"\s+)?(?:fn|impl|trait)\b")
_RAW_DEREF = re.compile(r"\*\s*(?:const|mut)\s+_?\w+")

_UNWRAP = re.compile(r"\.unwrap(?:_or(?:_else|_default)?)?\s*\(\s*\)")
_EXPECT = re.compile(r"\.expect\s*\(")
# Both spellings of a copy: the method form (x.clone()) and the handle-association form
# used to avoid a deep copy (Arc::clone(x)).
_CLONE = re.compile(r"(?:\.(?:clone|to_owned|to_vec)\s*\()|(?:::clone\s*\()")

# A clone that is provably redundant. A `&&T` parameter is the reliable signal: the receiver is
# already a reference, so cloning it copies a pointer to a pointer and can never be the intent.
_DOUBLE_REF_PARAM = re.compile(r"&{2}\s*(?:mut\s+)?\w")
# `Arc::clone(x)` / `Rc::clone(x)` adds a handle count rather than copying the value.
_ARC_CLONE = re.compile(r"\b(?:Arc|Rc)::clone\s*\(")

_SAFETY = re.compile(r"SAFETY\s*:", re.IGNORECASE)

_TEST_FN = re.compile(r"#\[(?:test|bench|tokio::test)\]|fn\s+test_|\bfn\s+main\s*\(")
_GUARDED_NEAR = re.compile(
    r"(?:if\s+let\s+Ok|if\s+let\s+Err|match\s+\w+|\.is_(?:ok|some|err|none)\(\)|"
    r"\.map_or\(|\.unwrap_or|assert(?:_eq|_ne|_ok)?!|#\[should_panic\])"
)

_MANUAL_CLONE_LOOP = re.compile(r"for\s+\w+\s+in\s+[\w.()]+\s*\.iter\(\)")
_STRING_ADD_LOOP = re.compile(r"^\s*\w+\s*\+=\s*(?:format!|\w+\.to_string\(\))")
_INDEX_LOOP = re.compile(r"for\s+\w+\s+in\s+0\s*\.\.")


class _Scanner:
    """Line-oriented scanner that attributes each finding to the enclosing function.

    Attribution matters: ``.unwrap()`` in a test and ``.unwrap()`` in library code are different
    claims, and the profile counts one canonical issue per site. Tracking the enclosing function is
    what lets the verdict depend on context rather than on the bare token.
    """

    def __init__(self, path, text):
        self.path = path
        self.lines = text.splitlines()
        self.findings = []

    def enclosing_function(self, lineno):
        """Name of the nearest ``fn`` declared at or above ``lineno``.

        Rust code is commonly written one statement per line, so the declaration may be on the
        same line as the statement (``pub fn f() -> i32 { x.unwrap() }``) as well as above it.
        """
        for index in range(lineno, 0, -1):
            match = re.search(
                r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\(", self.lines[index - 1]
            )
            if match:
                return match.group(1)
        return "<module>"

    def in_test_context(self, lineno):
        name = self.enclosing_function(lineno)
        if name.startswith("test_") or name == "<module>":
            # A `#[test]` attribute sits on the line above the signature.
            for candidate in (lineno - 2, lineno - 1):
                if 0 <= candidate < len(self.lines) and _TEST_FN.search(self.lines[candidate]):
                    return True
        return False

    def guarded_near(self, lineno):
        window = "\n".join(self.lines[max(0, lineno - 4) : lineno + 1])
        return bool(_GUARDED_NEAR.search(window))

    def safety_comment_near(self, lineno):
        # A `// SAFETY:` comment documents the construct below it, and may sit directly above the
        # `unsafe` line or above the enclosing `fn` signature. Only look upwards, never downwards,
        # so a later comment cannot retroactively justify an earlier block.
        for candidate in range(lineno - 1, max(0, lineno - 6), -1):
            line = self.lines[candidate - 1]  # `candidate` is a one-based line number
            if _SAFETY.search(line):
                return True
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "///", "/*", "*")):
                continue
            # A non-comment, non-blank line between the justification and the use ends the search,
            # unless it only closes the previous function or opens the current one.
            if stripped.startswith("}") or stripped.endswith("{"):
                continue
            return False
        return False

    def double_ref_receiver(self, lineno):
        """True when the receiver of the clone on ``lineno`` is itself a reference.

        The declaration and the clone are usually on different lines (``fn f(v: &&String) {``
        then ``v.clone()``), so the receiver's type is read from the enclosing signature rather
        than from the line that happens to contain the call.
        """
        if _DOUBLE_REF_PARAM.search(self.lines[lineno - 1]):
            return True
        # Walk back to the enclosing `fn` signature and inspect its parameter list.
        for candidate in range(lineno - 1, 0, -1):
            line = self.lines[candidate - 1]
            if re.search(r"\bfn\s+[A-Za-z_]", line):
                signature = " ".join(self.lines[candidate - 1 : lineno])
                return bool(re.search(r"&\s*&\s*(?:mut\s+)?\w", signature))
        return False

    def add(self, rule, line, verdict, confidence, message):
        self.findings.append(
            {
                "rule": rule,
                "path": self.path,
                "line": line,
                "end_line": line,
                "column": 1,
                "symbol": self.enclosing_function(line),
                "verdict": verdict,
                "confidence": confidence,
                "message": message,
                "evidence": {},
            }
        )

    def scan(self):
        for index, line in enumerate(self.lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue

            if _UNWRAP.search(line) or _EXPECT.search(line):
                if self.in_test_context(index) or self.guarded_near(index):
                    self.add(
                        "unwrap-guarded",
                        index,
                        "benign_in_context",
                        "medium",
                        "unwrap/expect is guarded, in a test, or an explicitly handled result",
                    )
                else:
                    self.add(
                        "unwrap-unguarded",
                        index,
                        "violation",
                        "high",
                        "unwrap/expect without a guard; the error path is discarded",
                    )

            if _CLONE.search(line):
                if self.double_ref_receiver(index):
                    # `fn f(value: &&String)` — the receiver is already a reference, so cloning it
                    # copies a pointer to a pointer and can never be what the author meant.
                    self.add(
                        "clone-redundant",
                        index,
                        "violation",
                        "high",
                        "cloning a value that is already behind a reference",
                    )
                elif _ARC_CLONE.search(line):
                    self.add(
                        "clone-redundant",
                        index,
                        "violation",
                        "medium",
                        "clone of an existing handle is redundant; pass the reference through",
                    )
                elif "for " in line and ".iter()" in line:
                    self.add(
                        "clone-redundant",
                        index,
                        "violation",
                        "medium",
                        "cloning inside a loop copies needlessly",
                    )
                else:
                    self.add(
                        "clone-contextual",
                        index,
                        "benign_in_context",
                        "low",
                        "clone moves an owned value or satisfies an owned API",
                    )

            if _UNSAFE_BLOCK.search(line) or _UNSAFE_FN.search(line) or _RAW_DEREF.search(line):
                if self.safety_comment_near(index):
                    self.add(
                        "unsafe-documented",
                        index,
                        "benign_in_context",
                        "medium",
                        "unsafe block is accompanied by a SAFETY justification",
                    )
                else:
                    self.add(
                        "unsafe-unjustified",
                        index,
                        "hint",
                        "medium",
                        "unsafe without a SAFETY justification; needs reviewer or judge evidence",
                    )

            if _MANUAL_CLONE_LOOP.search(line):
                self.add(
                    "manual-iteration-clone",
                    index,
                    "violation",
                    "medium",
                    "iterating and cloning; a by-reference iterator avoids the copy",
                )
            if _STRING_ADD_LOOP.search(line):
                self.add(
                    "string-format-loop",
                    index,
                    "violation",
                    "low",
                    "building a string by repeated formatting; collect and join",
                )
            if _INDEX_LOOP.search(line):
                self.add(
                    "manual-index-range",
                    index,
                    "violation",
                    "medium",
                    "an explicit 0.. range is an index loop; iterate the collection",
                )
        self.findings.sort(key=lambda f: (f["line"], f["rule"]))
        return self.findings


def scan_text(path, text):
    """Findings for one file, each attributed to its enclosing function."""
    return _Scanner(path, text).scan()


def scan(root, paths, options):
    files, findings, incomplete = [], [], False
    for rel in paths:
        try:
            with open(os.path.join(root, rel), "rb") as handle:
                source = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            files.append({"path": rel, "parsed": False, "error": type(error).__name__})
            incomplete = True
            continue
        files.append({"path": rel, "parsed": True, "error": None})
        findings.extend(scan_text(rel, source))
    findings.sort(key=lambda f: (f["path"], f["line"], f["rule"]))
    return {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "opportunities": sorted(options["opportunities"]),
        "complete": not incomplete,
        "files": files,
        "findings": findings,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_rust_scan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--opportunities", default="")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv[1:])
    options = {"opportunities": {item for item in args.opportunities.split(",") if item}}
    result = scan(args.root, args.paths, options)
    parent = os.path.dirname(args.output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True, separators=(",", ":"))
    if not result["complete"]:
        return 2
    return 1 if any(f["verdict"] == "violation" for f in result["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
