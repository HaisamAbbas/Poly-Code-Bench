"""Context-aware Go idiom scanner (pure stdlib line analysis; ships inside the images).

Go's anti-patterns are mostly invisible to a token-level linter, which is exactly why they must not
be counted automatically:

* **ignored errors** - discarding an error is a violation only when the discarded call actually
  *returns* one. The scanner resolves that from the function's own declaration or from the pinned
  standard-library signatures, never from the name alone; a call that returns nothing is not
  evidence. ``defer f.Close()`` is the deliberate exception: it is reported as a ``hint``.
* **broken error chains** - ``fmt.Errorf("...: %v", err)`` loses the wrapped error; ``%w`` keeps it.
  A format string with no error operand is not a violation.
* **sentinel comparison** - ``err == io.EOF`` breaks under wrapping; ``errors.Is`` does not.
  ``err == nil`` is correct Go and is never reported.
* **cancellation** - replacing a ``ctx`` the caller passed with ``context.Background()`` discards
  the caller's cancellation; a function that takes a ``ctx`` and blocks in a loop without ever
  consulting ``ctx.Done()`` cannot be cancelled; ``context.WithCancel`` without a deferred
  ``cancel()`` leaks the context for the lifetime of the parent.
* **goroutine lifecycle** - a goroutine that is never joined (no WaitGroup/errgroup, no channel
  handshake) cannot be observed, stopped or waited for. A goroutine inside a loop body is reported
  separately, because that one is also a leak per iteration.
* **standard library** - hand-rolled search/sort/string building where a stdlib call says it in one
  line.

Records carry ``violation`` (counts), ``benign_in_context`` (evidence, never penalised) or ``hint``
(needs reviewer or judge evidence). Counting constructs *used* never earns credit: the scanner only
reports defects; opportunity counts come from the frozen task.

Usage: python pcb_go_scan.py --root DIR --output FILE [--opportunities a,b] PATH...
Exit: 0 clean, 1 violations, 2 incomplete.
"""

import argparse
import json
import os
import re
import sys

SCANNER_VERSION = "1"
SCHEMA = "pcb-go-scan-v1"

# ------------------------------------------------------------------------------------- lexing

_LITERAL = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|`(?:[^`\\]|\\.)*`"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)

_FUNC = re.compile(r"^func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")
_CTX_TYPE = re.compile(r"\b(?:context\.)?Context\b")
_CTX_PARAM = re.compile(r"\bctx\b|\bcx\b|\bcancel\b|\bcontext\b")
_BG_CTX = re.compile(r"context\.(?:Background|TODO)\s*\(\s*\)")
_DONE = re.compile(r"\bctx\s*\.\s*Done\s*\(|\bctx\s*\.\s*Err\s*\(|\bcancel\s*\(\s*\)")

_ERROR_IFACE = re.compile(r"\berror\b")
_RETURNS_ERROR = re.compile(r"\)\s*(?:\([^)]*\berror\b[^)]*\)|error)\s*\{?\s*$")

# Standard-library functions whose (last) result is an error. Pinned deliberately small: a
# guessed signature would invent violations, and a false violation is worse than a missed hint.
STDLIB_ERROR_FUNCTIONS = frozenset(
    {
        "Close",
        "Copy",
        "Decode",
        "Encode",
        "Flush",
        "Mkdir",
        "MkdirAll",
        "NewDecoder.Decode",
        "Parse",
        "ParseInt",
        "ParseUint",
        "ParseFloat",
        "Read",
        "ReadAll",
        "ReadFile",
        "Remove",
        "RemoveAll",
        "Rename",
        "Scan",
        "Unmarshal",
        "Write",
        "WriteFile",
        "WriteString",
    }
)

# ------------------------------------------------------------------------------------- rules

_BLANK_ERROR = re.compile(r"(?<![:\w])_{1,2}\s*(?:,\s*_{1,2}\s*)*=(?!=)\s*(?P<call>[^=\n].*)$")
_DEFER_ERROR = re.compile(r"^\s*defer\s+(?P<call>[A-Za-z_][\w.]*\s*\()")
_DEFER_CALL = re.compile(r"^\s*defer\s+")
_ERRORF_V = re.compile(r"fmt\.Errorf\(\s*\"(?:[^\"\\]|\\.)*%[+#\-0-9.]*[vst](?:[^\"\\]|\\.)*\"")
_ERRORF_W = re.compile(r"fmt\.Errorf\(\s*\"(?:[^\"\\]|\\.)*%w")
_SENTINEL_CMP = re.compile(r"\berr\s*(?:==|!=)\s*(?!nil\b)([A-Za-z_][\w.]*)")
_ERR_ARG = re.compile(r",\s*(?:err|e|cause|innerErr)\b\s*\)?\s*$", re.MULTILINE)
_SLEEP = re.compile(r"\btime\s*\.\s*Sleep\s*\(|\btime\s*\.\s*After\s*\(")
_LOOP = re.compile(r"^\s*for\b")
_RANGE = re.compile(r"^\s*for\b[^\n]*\brange\b")
_GOROUTINE = re.compile(r"^\s*go\s+(?:func\s*\(|(?P<call>[A-Za-z_][\w.]*\s*\())")
_GO_CALL = re.compile(r"^\s*go\s+(?P<name>[A-Za-z_][\w.]*)\s*\(")
_JOIN_MARKER = re.compile(
    r"\bwg\s*\.\s*(?:Add|Done|Wait)\s*\(|\berrgroup\s*\.|"
    r"\bclose\s*\(|\bdefer\s+\w+\s*\.\s*Done\s*\("
)
_WITH_CANCEL = re.compile(r"\bcontext\s*\.\s*With(?:Cancel|Timeout|Deadline|TimeoutCause)\s*\(")
_CANCEL_CALL = re.compile(r"\b(?:cancel|cancelFunc)\s*\(\s*\)")
_EMPTY_INTERFACE_PARAM = re.compile(r"\b(?:interface\s*\{\s*\}|\bany)\b")
_SORT_SLICE = re.compile(r"\bsort\s*\.\s*Slice\s*\(")
_SPRINTF = re.compile(r"\bfmt\s*\.\s*Sprintf\s*\(")
_STRING_APPEND = re.compile(r"^\s*\w+\s*\+=\s*")
_MANUAL_SEARCH = re.compile(
    r"\bstrings\s*\.\s*(?:Index|Contains|HasPrefix|HasSuffix|TrimPrefix)\s*\("
)

# `defer` at the top level of a function body is not "inside a loop".
_LOOP_DEPTH_STEP = re.compile(r"\{\s*$")


def _blank(match):
    """Keep every newline so reported line numbers still match the real file."""
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text):
    """Blank out comments and literals, preserving line structure."""
    return _LITERAL.sub(_blank, text)


class _Function:
    __slots__ = ("name", "start", "end", "signature", "body", "ctx", "returns_error")

    def __init__(self, name, start, signature):
        self.name = name
        self.start = start
        self.end = start
        self.signature = signature
        self.body = ""
        self.ctx = False
        self.returns_error = False


class _Scanner:
    """Line-oriented scanner that attributes each finding to the enclosing function.

    Attribution is what lets a verdict depend on context: a discarded error inside a helper is a
    different claim from one inside ``main``, and a goroutine started in a function that joins it
    is not a leak.
    """

    def __init__(self, path, text):
        self.path = path
        self.raw = text.splitlines()
        self.lines = sanitize(text).splitlines()
        self.findings = []
        self.functions = self._functions()
        self.local_error_functions = {
            f.name for f in self.functions if f.name != "<toplevel>" and f.returns_error
        }
        self.loop_lines = self._loop_lines()

    # ----------------------------------------------------------------- structure

    def _functions(self):
        found = []
        for index, line in enumerate(self.lines, start=1):
            match = _FUNC.match(line)
            if not match:
                continue
            signature = self._signature(index)
            function = _Function(match.group("name"), index, signature)
            function.ctx = bool(_CTX_TYPE.search(signature)) and bool(_CTX_PARAM.search(signature))
            results = signature.split(")", 1)[-1]
            function.returns_error = "error" in results
            found.append(function)
        for position, function in enumerate(found):
            stop = found[position + 1].start if position + 1 < len(found) else len(self.lines) + 1
            function.end = max(function.start, stop - 1)
            function.body = "\n".join(self.lines[function.start - 1 : function.end])
        return found

    def _signature(self, index):
        """Join the parameter list across the lines a declaration spans."""
        parts = [self.lines[index - 1]]
        cursor = index
        while parts[-1].count("(") > parts[-1].count(")") and cursor < len(self.lines):
            cursor += 1
            parts.append(self.lines[cursor - 1])
        return " ".join(parts)

    def _loop_lines(self):
        """Line numbers that sit inside a loop body, by brace depth."""
        depth = 0
        loop_depth = None
        inside = set()
        for index, line in enumerate(self.lines, start=1):
            stripped = line.strip()
            is_loop = bool(_LOOP.match(line)) and not stripped.startswith("//")
            opens = line.count("{") - line.count("}")
            if is_loop and opens >= 0:
                loop_depth = depth
                depth += max(opens, 0)
                continue
            if loop_depth is not None:
                if depth > loop_depth:
                    inside.add(index)
                depth += opens
                if depth <= loop_depth:
                    loop_depth = None
        return inside

    def enclosing_function(self, lineno):
        chosen = None
        for function in self.functions:
            if function.start <= lineno:
                chosen = function
            else:
                break
        return chosen

    def in_loop(self, lineno):
        return lineno in self.loop_lines

    def returns_error_call(self, call):
        """Whether a discarded call's callee is known to return an error."""
        name = call.strip()
        name = name.split("(", 1)[0].strip() if "(" in name else name
        bare = name.split(".")[-1]
        if name.startswith(("ctx.", "time.")) or "." not in name:
            if name in self.local_error_functions:
                return True
        return bare in STDLIB_ERROR_FUNCTIONS or f"{name}.Close" in STDLIB_ERROR_FUNCTIONS

    # ------------------------------------------------------------------- reporting

    def add(self, rule, line, verdict, confidence, message, symbol=None):
        self.findings.append(
            {
                "rule": rule,
                "path": self.path,
                "line": line,
                "end_line": line,
                "column": 1,
                "symbol": symbol or "<module>",
                "verdict": verdict,
                "confidence": confidence,
                "message": message,
                "evidence": {},
            }
        )

    # ---------------------------------------------------------------------- rules

    def scan(self):
        for index in range(1, len(self.lines) + 1):
            raw = self.raw[index - 1]
            clean = self.lines[index - 1]
            if raw.strip().startswith("//"):
                continue
            function = self.enclosing_function(index)
            symbol = function.name if function else "<toplevel>"
            self._ignored_error(index, raw, symbol)
            self._error_chain(index, raw, symbol)
            self._sentinel_comparison(index, clean, symbol)
            self._context(index, clean, function, symbol)
            self._goroutine(index, clean, function, symbol)
            self._loop_body(index, raw, clean, function, symbol)
            self._api(index, clean, symbol)
        self._context_functions()
        self.findings.sort(key=lambda f: (f["line"], f["rule"]))
        return self.findings

    def _ignored_error(self, index, raw, symbol):
        deferred = _DEFER_ERROR.search(raw)
        if deferred is not None:
            # `defer f.Close()` is idiomatic: the cleanup runs whether or not the body returns.
            # A reviewer confirms it was meant to be best-effort; it is never counted.
            if self.returns_error_call(deferred.group("call")):
                self.add(
                    "error-ignored-deferred",
                    index,
                    "hint",
                    "medium",
                    "a deferred call discards its error; a reviewer should confirm the cleanup "
                    "is deliberately best-effort",
                    symbol,
                )
            return
        match = _BLANK_ERROR.search(raw)
        if not match:
            return
        if not self.returns_error_call(match.group("call")):
            return
        self.add(
            "error-ignored-blank",
            index,
            "violation",
            "high",
            "the error result of this call is discarded by assignment to the blank identifier",
            symbol,
        )

    def _error_chain(self, index, raw, symbol):
        if not _ERRORF_V.search(raw) or _ERRORF_W.search(raw):
            return
        if not _ERR_ARG.search(raw):
            # A format string that carries no error operand is not a broken chain.
            return
        self.add(
            "error-chain-broken",
            index,
            "violation",
            "high",
            "fmt.Errorf formats the error with %v, so errors.Is/errors.As cannot see the cause; "
            "use %w",
            symbol,
        )

    def _sentinel_comparison(self, index, clean, symbol):
        match = _SENTINEL_CMP.search(clean)
        if not match:
            return
        self.add(
            "error-sentinel-comparison",
            index,
            "violation",
            "medium",
            "comparing an error with == breaks once the error is wrapped; use errors.Is",
            symbol,
        )

    def _context(self, index, clean, function, symbol):
        if _BG_CTX.search(clean):
            if function is not None and function.ctx:
                self.add(
                    "context-replaced",
                    index,
                    "violation",
                    "high",
                    "a fresh background context is created although the function already receives "
                    "a context; the caller's cancellation is discarded",
                    symbol,
                )
            else:
                self.add(
                    "context-created-at-entry",
                    index,
                    "benign_in_context",
                    "medium",
                    "this function is the entry point, so it is where a root context belongs",
                    symbol,
                )
        if _WITH_CANCEL.search(clean) and not _CANCEL_CALL.search(
            function.body if function else clean
        ):
            self.add(
                "context-cancel-not-deferred",
                index,
                "violation",
                "high",
                "the context returned here is never cancelled; the cancellation is retained "
                "until the parent context is done",
                symbol,
            )

    def _context_functions(self):
        """Context rules about a function as a whole, reported once at its declaration."""
        for function in self.functions:
            if not function.ctx:
                continue
            params = function.signature[
                function.signature.find("(") + 1 : function.signature.rfind(")")
            ]
            if not re.match(r"^\s*(?:\w+\s+)?\b(?:ctx|cx)\b", params):
                self.add(
                    "context-not-first-parameter",
                    function.start,
                    "violation",
                    "low",
                    "a context should be the first parameter so the call reads in the order it "
                    "cancels",
                    function.name,
                )
            if not function.body.strip():
                continue
            blocks = re.search(r"^\s*for\b", function.body, re.MULTILINE) or _SLEEP.search(
                function.body
            )
            if blocks and not _DONE.search(function.body):
                self.add(
                    "context-not-honoured",
                    function.start,
                    "violation",
                    "high",
                    "this function blocks in a loop or sleep but never consults ctx.Done(), so it "
                    "cannot be cancelled",
                    function.name,
                )

    def _goroutine(self, index, clean, function, symbol):
        match = _GOROUTINE.search(clean)
        if not match:
            return
        call = match.group("call")
        joined = _JOIN_MARKER.search(function.body if function else clean)
        if call is None and not joined:
            self.add(
                "goroutine-not-joined",
                index,
                "violation",
                "high",
                "this goroutine is never joined: nothing waits for it and its result cannot be "
                "observed",
                symbol,
            )
            return
        if self.in_loop(index) and not joined:
            self.add(
                "goroutine-in-loop",
                index,
                "violation",
                "high",
                "a goroutine started inside a loop body is not joined, so one is left running per "
                "iteration",
                symbol,
            )

    def _loop_body(self, index, raw, clean, function, symbol):
        if not self.in_loop(index):
            return
        if _SLEEP.search(clean):
            self.add(
                "sleep-in-loop",
                index,
                "violation",
                "medium",
                "sleeping inside a loop occupies the goroutine for the whole duration instead of "
                "waiting on a channel or a context",
                symbol,
            )
        if _SPRINTF.search(clean) or _STRING_APPEND.match(clean):
            rule = "sprintf-in-loop" if _SPRINTF.search(clean) else "accumulator-in-loop"
            self.add(
                rule,
                index,
                "violation",
                "medium",
                "a value is extended or formatted one item at a time inside the loop; a single "
                "preallocation (a sized slice, strings.Builder) avoids the repeated work",
                symbol,
            )
        if _RANGE.match(clean) and _MANUAL_SEARCH.search(clean):
            self.add(
                "manual-string-search",
                index,
                "violation",
                "low",
                "a manual scan over a string where the standard library has a direct call",
                symbol,
            )

    def _api(self, index, clean, symbol):
        head = clean.split("{", 1)[0]
        if _FUNC.match(clean) and _EMPTY_INTERFACE_PARAM.search(head):
            self.add(
                "empty-interface-parameter",
                index,
                "hint",
                "medium",
                "interface{} in a signature accepts anything and guarantees nothing; a small named "
                "interface or a type parameter states the contract",
                symbol,
            )
        if _SORT_SLICE.search(clean) and self.in_loop(index):
            self.add(
                "sort-in-loop",
                index,
                "hint",
                "low",
                "sorting inside a loop; sorting once after the loop (or slices.SortFunc) is both "
                "faster and clearer",
                symbol,
            )


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
    parser = argparse.ArgumentParser(prog="pcb_go_scan")
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
    return 1 if any(item["verdict"] == "violation" for item in result["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
