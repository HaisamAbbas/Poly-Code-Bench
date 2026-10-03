"""Context-aware JavaScript/TypeScript idiom scanner (pure stdlib token analysis; ships in the images).

The families reported here are the ones a token-level linter can only *suspect*, because each is a
property of the surrounding code rather than of a single token:

* async - ``floating-promise`` (a promise-producing expression used as a bare statement),
  ``missing-await`` (an async call consumed inside another async function without ``await``),
  ``unhandled-rejection`` (a ``.then(`` chain with no ``catch(`` downstream),
  ``unbounded-sequential-await`` (a sequential ``await`` over a literal collection) and
  ``redundant-await-in-try`` (``return await`` inside a ``try`` that has a ``catch``);
* mutation - ``var-redeclaration``, ``needless-mutation``, ``exported-mutable-binding`` and
  ``import-time-side-effect``;
* security - ``dynamic-eval``, ``command-injection``, ``hardcoded-credential``,
  ``prototype-pollution``, ``unsafe-html``, ``unsafe-json-parse`` and ``unvalidated-path-join``;
* lint confirmation - ``loose-equality`` between operands of different *apparent* kinds. The
  idiomatic ``x == null`` / ``x != null`` null check is deliberately never reported: the ESLint
  rule suspects it, the scanner confirms nothing, so the family does not fire there;
* TypeScript only, never on ``.js``/``.mjs``/``.cjs`` - ``any-escape``.

Two decisions are worth stating because they shape the whole file.

First, the scanner is static: the code it reads is never executed, imported or resolved, and every
family is decided from tokens. Second, it is *confirming*, not *suspecting*: a family fires only
when the surrounding evidence is established, and an unresolvable construct is left alone. The cost
is a miss on code the scanner cannot resolve; the benefit is that a reported site is a real site,
which is what lets a token-level lint finding and this finding collapse to one canonical issue.
``unbounded-sequential-await`` in particular is a composition opportunity that was not taken, not a
measured performance regression.

Comments, string literals, template literals and regex literals are blanked - the same approach as
the Rust ``symbols.sanitize`` - while every newline and every column is preserved, so no family can
fire from inside a string or a comment. A literal's *delimiters* survive the mask, because "this
argument is a template literal" is a condition several families need while its content must stay
unread.

This is a structural scan, not a JavaScript parser. It tokenises the masked source and walks it by
brace and statement boundaries, and it resolves nesting only where the evidence requires it. A file
whose braces or parentheses do not balance is listed in ``parse_failures`` and contributes no
findings, because a report built from an unbalanced prefix is a guess.

Usage: python pcb_js_scan.py --root DIR --output FILE PATH...

Output::

    {"schema": "pcb-js-scan-v1", "language": ..., "scanned": [...], "findings": [...],
     "parse_failures": [...]}

with each finding ``{"check_id", "path", "line", "column", "family", "severity",
"explanation"}`` and ``check_id`` ``<language>.context.<family>``, where ``language`` is
``javascript`` or ``typescript`` taken from the file's own extension. A path whose extension is
neither is a ``parse_failure``, not a clean scan: the scanner must never report "no defects" for a
file it cannot attribute to a language.

Exit: 0 when the scan ran, *including* when it found defects - findings are data, not failures -
and 2 when the scanner could not run or a file could not be read or could not be walked.
"""

import argparse
import json
import os
import re
import sys

SCANNER_VERSION = "1"
SCHEMA = "pcb-js-scan-v1"

_JAVASCRIPT = frozenset((".js", ".mjs", ".cjs"))
_TYPESCRIPT = frozenset((".ts", ".mts", ".cts", ".tsx"))
_LANGUAGE = dict.fromkeys(_JAVASCRIPT, "javascript")
_LANGUAGE.update(dict.fromkeys(_TYPESCRIPT, "typescript"))

_SEVERITY = {
    "command-injection": "critical",
    "dynamic-eval": "high",
    "hardcoded-credential": "high",
    "prototype-pollution": "high",
    "floating-promise": "high",
    "missing-await": "high",
    "unhandled-rejection": "high",
    "var-redeclaration": "medium",
    "needless-mutation": "medium",
    "exported-mutable-binding": "medium",
    "import-time-side-effect": "medium",
    "loose-equality": "medium",
    "unsafe-html": "medium",
    "unsafe-json-parse": "medium",
    "unvalidated-path-join": "medium",
    "any-escape": "medium",
    "redundant-await-in-try": "low",
    "unbounded-sequential-await": "low",
}

# One alternation, scanned left to right, so whichever construct starts first wins: a `//` inside a
# string is string content, and a `"` inside a regex literal does not open a string. Each literal
# is masked into a same-length run of spaces (a template keeps its backticks and `${` markers), so
# line numbers and columns of the surrounding code are untouched.
_LITERAL = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|`(?:[^`\\$]|\\.|\$(?!\{)|\$\{(?:[^{}`\\]|\\.)*\})*`"
    r"|\"(?:\\.|[^\"\\\n])*\""
    r"|'(?:\\.|[^'\\\n])*'"
    r"|/(?![*/\n])(?:\[(?:\\.|[^\]\\\n])*\]|\\.|[^/\\\n])+/[a-z]*",
    re.DOTALL,
)
# A `/` opens a regex literal only where an operand is not already complete: `last` is the last
# significant character seen, so a slash after an identifier or a closing bracket stays a division
# and a slash after `(`, `=`, `,` or a statement keyword is a regex.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%~^<>")
_REGEX_KEYWORDS = frozenset(
    (
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "do",
        "else",
        "yield",
        "await",
        "case",
    )
)

_TOKEN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<number>\d[\w.]*)"
    r"|(?P<name>[A-Za-z_$][\w$]*)"
    r"|(?P<operator>>>>=|<<=|>>=|===|!==|\*\*=|&&=|\|\|=|\?\?=|>>>|=>|==|!=|<=|>=|&&|\|\||\?\?|"
    r"\*\*|<<|>>|\+\+|--|\+=|-=|\*=|/=|%=|=>|\?\.|\.\.\.)"
    # A single-character operator is a punctuation token, never an "other": the walk decides block
    # versus object literal from the token kind, and `other` would hide `,` `.` `(` `)` from it.
    r"|(?P<punct>[^\s\w$])",
    re.DOTALL,
)

# Tokens after which a `{` opens an object or destructuring literal rather than a block. The
# comparison and generic operators are deliberately absent: a body brace after `): Promise<T>`
# follows `>`, and treating that as a literal would swallow the whole function body.
_LITERAL_BRACE_PUNCT = frozenset(
    (
        "=",
        "(",
        ",",
        ":",
        "[",
        "?",
        "??",
        "&&",
        "||",
        "!",
        "...",
        "+",
        "-",
        "*",
        "/",
        "%",
        "+=",
        "-=",
        "*=",
        "??=",
        "||=",
        "&&=",
    )
)
_LITERAL_BRACE_NAME = frozenset(
    (
        "default",
        "export",
        "import",
        "return",
        "from",
        "of",
        "in",
        "typeof",
        "await",
        "yield",
        "new",
        "delete",
        "void",
        "const",
        "let",
        "var",
        "throw",
    )
)
# A statement may continue onto the next line after one of these; anywhere else, a statement
# keyword on a new line starts a new statement (automatic semicolon insertion).
_CONTINUATION = frozenset(
    (
        ".",
        "?.",
        "[",
        "(",
        ",",
        ":",
        "?",
        "=>",
        "=",
        "+",
        "-",
        "*",
        "/",
        "%",
        "**",
        "&&",
        "||",
        "??",
        "&",
        "|",
        "^",
        "==",
        "!=",
        "===",
        "!==",
        "<",
        ">",
        "<=",
        ">=",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&&=",
        "||=",
        "??=",
        "**=",
        "<<=",
        ">>=",
        ">>>=",
        "...",
        "++",
        "--",
        "!",
    )
)
_STATEMENT_STARTERS = frozenset(
    (
        "if",
        "for",
        "while",
        "do",
        "switch",
        "try",
        "catch",
        "finally",
        "else",
        "return",
        "throw",
        "break",
        "continue",
        "const",
        "let",
        "var",
        "function",
        "class",
        "import",
        "export",
        "case",
        "default",
        "debugger",
        "interface",
        "type",
        "enum",
        "namespace",
        "declare",
        "abstract",
    )
)
_CONTROL_HEADERS = frozenset(("if", "for", "while", "switch", "catch"))
_DECLARATION_KEYWORDS = frozenset(("const", "let", "var"))
_ASSIGNMENT_OPERATORS = frozenset(
    (
        "=",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&&=",
        "||=",
        "??=",
        "**=",
        "<<=",
        ">>=",
        ">>>=",
    )
)
_BINARY_OPERATORS = frozenset(
    (
        "+",
        "-",
        "*",
        "/",
        "%",
        "==",
        "!=",
        "===",
        "!==",
        "<",
        ">",
        "<=",
        ">=",
        "&&",
        "||",
        "??",
        "&",
        "|",
        "^",
        "**",
    )
)
_CHAIN_METHODS = ("then", "catch", "finally")
_CREDENTIAL = re.compile(
    r"(?:^|[^a-z])(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"credential|bearer)(?:$|[^a-z])",
    re.IGNORECASE,
)
_CHILD_PROCESS_CALLS = frozenset(
    (
        "exec",
        "execSync",
        "spawn",
        "spawnSync",
        "execFile",
        "execFileSync",
    )
)
_TIMER_CALLS = frozenset(("setTimeout", "setInterval"))
_LISTENER_METHODS = frozenset(
    (
        "addEventListener",
        "removeEventListener",
        "addListener",
        "prependListener",
        "once",
        "on",
    )
)
_HTML_PROPERTIES = frozenset(("innerHTML", "outerHTML"))
_PATH_METHODS = frozenset(("join", "resolve", "normalize", "relative"))
_PATH_RECEIVER = re.compile(r"\A(?:[A-Za-z_$][\w$]*)?[Pp]ath\Z")
_GUARD_WORDS = ("typeof", "instanceof", "Array", "isArray", "hasOwnProperty", "in", "inArray")
_SIGNATURE_WORDS = frozenset(
    (
        "function",
        "=>",
        "constructor",
        "export",
        "declare",
        "abstract",
        "interface",
        "type",
        "public",
        "protected",
        "private",
        "readonly",
        "static",
        "get",
        "set",
        "async",
        "class",
    )
)
_OPENERS, _CLOSERS = ("(", "[", "{"), (")", "]", "}")
_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


def _blank(text):
    return "".join("\n" if ch == "\n" else " " for ch in text)


def _mask_template(text):
    """Blank a template literal, keeping only its backticks.

    Keeping the backticks is what lets a family see that the argument *is* a template while its
    content stays unread, and it is what makes the tokenizer emit one literal token. Everything
    else, substitutions included, becomes a space, so the brace balance of the surrounding code is
    untouched: code inside ``${...}`` is deliberately not scanned, which leaves a nested template
    unresolved rather than mis-attributed.
    """
    return "`" + _blank(text[1:-1]) + "`"


def _mask_regex(text):
    match = re.match(r"/(?P<body>.*)/(?P<flags>[a-z]*)\Z", text, re.DOTALL)
    if match is None:  # pragma: no cover - _LITERAL guarantees the shape
        return _blank(text)
    return "/" + _blank(match.group("body")) + "/" + match.group("flags")


def sanitize(text):
    """Mask comments and literals, keeping every newline and every column.

    Returns the masked text and the ``(start, end, kind)`` spans of the literals that survive, so
    the tokenizer can emit each literal as one token with its original source still available. The
    Rust scanner blanks literals with a single substitution and forgets them; JavaScript has to keep
    the spans, because "is this a string, a template or a regex" is itself a finding condition.
    """
    out = []
    literals = []
    position = 0
    last = ""
    length = len(text)
    while position < length:
        match = _LITERAL.search(text, position)
        if match is None:
            chunk = text[position:]
            out.append(chunk)
            for ch in chunk:
                if not ch.isspace():
                    last = ch
            break
        start, end = match.span()
        chunk = text[position:start]
        out.append(chunk)
        for ch in chunk:
            if not ch.isspace():
                last = ch
        piece = match.group(0)
        if piece.startswith("//") or piece.startswith("/*"):
            out.append(_blank(piece))
        elif piece[0] == "`":
            out.append(_mask_template(piece))
            literals.append((start, end, "template"))
            last = "`"
        elif piece[0] == "/":
            if last and last not in _REGEX_PRECEDERS and last not in _REGEX_KEYWORDS:
                # A division, not a regex: emit the slash as punctuation and rescan.
                out.append("/")
                last = "/"
                position = start + 1
                continue
            out.append(_mask_regex(piece))
            literals.append((start, end, "regex"))
            last = "/"
        else:
            out.append(piece[0] + _blank(piece[1:-1]) + piece[-1])
            literals.append((start, end, "string"))
            last = piece[-1]
        position = end
    return "".join(out), literals


class _Token:
    """One token; ``raw`` is the original text, which literal tokens need for comparison."""

    __slots__ = ("kind", "value", "raw", "line", "column")

    def __init__(self, kind, value, raw, line, column):
        self.kind = kind
        self.value = value
        self.raw = raw
        self.line = line
        self.column = column

    def key(self):
        """A comparable rendering: a literal compares by its text, everything else by its value."""
        return self.raw if self.kind == "literal" else self.value

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<%s %r @%d:%d>" % (self.kind, self.value, self.line, self.column)


def tokenize(masked, source, literals):
    """Tokens of the masked source, with literal spans kept as single tokens."""
    spans = {start: (end, kind) for start, end, kind in literals}
    tokens = []
    position = 0
    line = 1
    column = 1
    length = len(masked)
    while position < length:
        span = spans.get(position)
        if span is None:
            match = _TOKEN.match(masked, position)
            if match is None:  # pragma: no cover - the `punct` branch matches everything
                position += 1
                continue
            group = match.lastgroup
            kind = "punct" if group == "operator" else group
            end = match.end()
            value = match.group(0)
        else:
            end, value = span
            kind = "literal"
        if kind != "space":
            tokens.append(_Token(kind, value, source[position:end], line, column))
        consumed = masked[position:end]
        newlines = consumed.count("\n")
        if newlines:
            line += newlines
            column = len(consumed) - consumed.rfind("\n")
        else:
            column += len(consumed)
        position = end
    return tokens


class _Unbalanced(ValueError):
    """A file whose braces or parentheses do not balance; it is reported, not guessed at."""


class _Frame:
    """One open brace: what it is, and the bindings and state its body owns."""

    __slots__ = (
        "kind",
        "async_fn",
        "open_index",
        "token",
        "is_try",
        "is_catch",
        "has_catch",
        "loop_name",
        "loop_elements",
        "bindings",
        "state",
        "pending_awaits",
    )

    def __init__(self, kind, async_fn, open_index, token):
        self.kind = kind
        self.async_fn = async_fn
        self.open_index = open_index
        self.token = token
        self.is_try = kind == "try"
        self.is_catch = kind == "catch"
        self.has_catch = False
        self.loop_name = ""
        self.loop_elements = 0
        self.bindings = {}
        self.state = {}
        self.pending_awaits = []


def _match(tokens, index, opener=_OPENERS, closer=_CLOSERS):
    """Index of the token closing the group opened at ``index``, or None when unbalanced.

    ``opener`` and ``closer`` may be a single token value or a set of them; the default matches
    any bracket with any bracket, which is what a structural walk wants.
    """
    if index >= len(tokens) or tokens[index].value not in opener:
        return None
    depth = 0
    for cursor in range(index, len(tokens)):
        token = tokens[cursor]
        if token.kind != "punct":
            continue
        if token.value in _OPENERS:
            depth += 1
        elif token.value in _CLOSERS:
            depth -= 1
            if depth == 0:
                return cursor if token.value in closer else None
    return None


def _open(tokens, index):
    """Index of the opener of the group closed at ``index``, or None."""
    pairs = _BRACKET_PAIRS
    wanted = pairs[tokens[index].value]
    depth = 0
    for cursor in range(index, -1, -1):
        token = tokens[cursor]
        if token.kind != "punct":
            continue
        if token.value in _CLOSERS:
            depth += 1
        elif token.value in _OPENERS:
            depth -= 1
            if depth == 0:
                return cursor if token.value == wanted else None
    return None


def _callee(tokens, start, index):
    """The callee named at ``index``: ``(base, members)``.

    Walks left over member accesses and bracket groups and stops at anything that is not a name,
    so ``f() + g()`` names ``g`` and ``a ? b() : c()`` names ``c``. A leading assignment operator
    or declaration keyword is skipped, because in ``const x = f()`` the expression starts at
    ``f``. ``members`` excludes the method being called and excludes a call in the chain, so
    ``obj.get(x).then(y)`` resolves to base ``obj`` with members ``[get]``.
    """
    begin = start
    while begin < index and tokens[begin].value in _ASSIGNMENT_OPERATORS | _DECLARATION_KEYWORDS:
        begin += 1
    cursor = index
    members = []
    while cursor >= begin:
        token = tokens[cursor]
        if token.value in (")", "]"):
            opener = _open(tokens, cursor)
            if opener is None:
                return None, []
            if token.value == ")":
                closer = _match(tokens, opener)
                if closer is not None and closer < cursor:
                    return None, []  # a call in the middle of the chain: not a resolvable callee
            cursor = opener - 1
            continue
        if token.value in (".", "?."):
            if cursor - 1 >= begin and tokens[cursor - 1].kind == "name":
                members.append(tokens[cursor - 1].value)
                cursor -= 2
                continue
            return None, []
        if token.kind == "name":
            members.append(token.value)
            cursor -= 1
            continue
        return None, []
    members.reverse()
    if not members:
        return None, []
    if members[0] in ("new", "await", "void", "typeof", "delete", "return", "yield"):
        return None, []
    return members[0], members[1:]


def _is_call_at(tokens, index, limit=None):
    """True when the name at ``index`` is the callee of a call whose ``(`` follows it."""
    stop = len(tokens) if limit is None else limit
    if index + 1 >= stop or tokens[index + 1].value != "(":
        return False
    if index and tokens[index - 1].value in (".", "?."):
        return False
    if index and tokens[index - 1].value == ")":
        return False
    return True


def _signature_is_async(statement):
    """True when the declaration in ``statement`` introduces an async function."""
    for index, token in enumerate(statement):
        if token.kind != "name" or token.value != "async":
            continue
        nxt = index + 1
        if nxt < len(statement) and statement[nxt].value == "function":
            return True
        if nxt < len(statement) and statement[nxt].value == "(":
            closer = _match(statement, nxt, "(", ")")
            if (
                closer is not None
                and closer + 1 < len(statement)
                and (statement[closer + 1].value == "=>")
            ):
                return True
        if nxt < len(statement) and statement[nxt].kind == "name" and statement[-1].value == "=>":
            return True  # `async x => ...`
    return False


def _return_type_is_promise(statement, closer):
    """True when the ``)`` at ``closer`` is followed by a Promise-typed return annotation."""
    index = closer + 1
    if index >= len(statement) or statement[index].value != ":":
        return False
    # The annotation runs to the body brace or the end of the signature; it must not stop at the
    # `<` of `Promise<T>`, because that generic argument list is part of the same type.
    end = index + 1
    while end < len(statement):
        value = statement[end].value
        if value == "{":
            break
        if value in ("}", ";", "=>") and end > index + 1:
            break
        end += 1
    names = [
        statement[i].value
        for i in range(index + 1, end)
        if statement[i].kind == "name" and statement[i].value in ("Promise", "PromiseLike")
    ]
    if not names:
        return False
    for i in range(index + 1, end):
        if statement[i].value in ("|", "&") and not any(
            statement[j].kind == "name" and statement[j].value in ("Promise", "PromiseLike")
            for j in range(i + 1, end)
        ):
            return False
    return True


def _promise_annotation(statement, index):
    """True when the ``:``/``?``/``readonly`` at ``index`` introduces a Promise type."""
    cursor = index + 1
    if cursor < len(statement) and statement[cursor].value == "readonly":
        cursor += 1
    return (
        cursor < len(statement)
        and statement[cursor].kind == "name"
        and statement[cursor].value
        in (
            "Promise",
            "PromiseLike",
        )
    )


def _element_count(statement, start, stop):
    """Number of top-level elements in ``statement[start:stop]``, or None if it is not a list."""
    if stop - start < 2 or statement[start].value != "[":
        return None
    closer = _match(statement, start, "[", "]")
    if closer is None or closer != stop - 1:
        return None
    # Inside `[start, closer)` the array's own bracket is already consumed, so a top-level element
    # separator sits at depth 0 and a nested collection raises the depth.
    count, depth, seen = 0, 0, False
    for index in range(start + 1, closer):
        value = statement[index].value
        if value in _OPENERS:
            depth += 1
        elif value in _CLOSERS:
            depth -= 1
        elif depth == 0:
            if value == ",":
                count += 1
            elif statement[index].kind != "space":
                seen = True
    if closer - 1 > start + 1 and statement[closer - 1].value == ",":
        count -= 1  # a trailing comma, or an elision in a hole
    return count + 1 if count >= 0 and seen else 0


def _literal_collection_size(statement, start, stop):
    """Element count of a literal collection, or 0 when it is not one with more than one element."""
    if stop - start >= 2 and statement[start].value == "[":
        count = _element_count(statement, start, stop)
        return count if count is not None and count > 1 else 0
    if (
        stop - start >= 5
        and statement[start].kind == "name"
        and statement[start].value == "Array"
        and (
            statement[start + 1].value == "."
            and statement[start + 2].value == "from"
            and statement[start + 3].value == "("
        )
    ):
        closer = _match(statement, start + 3, "(", ")")
        if closer is not None and closer == stop - 1:
            count, depth, seen = 0, 0, False
            for index in range(start + 4, closer):
                value = statement[index].value
                if value in _OPENERS:
                    depth += 1
                elif value in _CLOSERS:
                    depth -= 1
                if depth == 0 and value == ",":
                    count += 1
                elif depth == 0:
                    seen = True
            return count + 1 if seen and count + 1 > 1 else 0
    return 0


class _Scanner:
    """Walks one file's tokens and reports the families the surrounding evidence establishes."""

    def __init__(self, path, source):
        self.path = path
        self.language = _LANGUAGE.get(_suffix(path))
        self.findings = []
        masked, literals = sanitize(source)
        self.tokens = tokenize(masked, source, literals)
        self.declarations = {}
        self._collect_declarations()

    # -- promise evidence ------------------------------------------------------------------

    def _collect_declarations(self):
        """Record every declaration in the scanned set whose call is provably a promise."""
        tokens = self.tokens
        count = len(tokens)
        for index in range(count):
            token = tokens[index]
            if token.kind != "name" or token.value not in ("function", "class"):
                continue
            name_index = index + 1
            if name_index < count and tokens[name_index].value == "*":
                name_index += 1
            if name_index >= count or tokens[name_index].kind != "name":
                continue
            # The parameter list opens after the name, not at the name itself.
            closer = _match(tokens, name_index + 1, "(", ")")
            if closer is None:
                continue
            if token.value == "function":
                # The `async` modifier sits before the `function` keyword, so the signature the
                # async check reads has to start there, not at the keyword.
                start = index - 1 if index and tokens[index - 1].value == "async" else index
                promised = _return_type_is_promise(tokens, closer)
                self.declarations.setdefault(
                    tokens[name_index].value,
                    (_signature_is_async(tokens[start : closer + 1]), promised, token.line),
                )
                continue
            for cursor in range(index + 1, closer):
                if tokens[cursor].value in (":", "?", "<") and _promise_annotation(tokens, cursor):
                    self.declarations.setdefault(
                        tokens[name_index].value, (False, True, token.line)
                    )
                    break
        for index in range(count):
            if tokens[index].kind != "name" or tokens[index].value not in ("const", "let"):
                continue
            previous = tokens[index - 1].value if index else ""
            if previous in (";", "}", "export", "default", "{", "("):
                self._collect_arrow(index)

    def _collect_arrow(self, index):
        tokens = self.tokens
        count = len(tokens)
        declarator = index + 1
        if declarator >= count or tokens[declarator].kind != "name":
            return
        name = tokens[declarator].value
        cursor = declarator + 1
        promised = False
        while cursor < count and tokens[cursor].value in (":", "?", "readonly"):
            if _promise_annotation(tokens, cursor):
                promised = True
            cursor += 1
        if cursor >= count or tokens[cursor].value != "=":
            return
        cursor += 1
        if cursor >= count:
            return
        if tokens[cursor].kind == "name" and tokens[cursor].value == "async":
            self.declarations.setdefault(name, (True, promised, tokens[index].line))
            return
        head = cursor
        if head < count and tokens[head].value == "*":
            head += 1
        if head < count and tokens[head].value == "function":
            closer = _match(tokens, head + 1, "(", ")")
            if closer is not None and _signature_is_async(tokens[head : closer + 1]):
                self.declarations.setdefault(name, (True, promised, tokens[index].line))
            return
        if head < count and tokens[head].value == "(":
            closer = _match(tokens, head, "(", ")")
            if closer is not None and closer + 1 < count and tokens[closer + 1].value == "=>":
                if _signature_is_async(tokens[head : closer + 1]):
                    self.declarations.setdefault(name, (True, True, tokens[index].line))
                elif promised:
                    self.declarations.setdefault(name, (False, True, tokens[index].line))

    def _is_async_declaration(self, name):
        entry = self.declarations.get(name)
        return bool(entry[0]) if entry else False

    def _is_promise_declaration(self, name):
        entry = self.declarations.get(name)
        return bool(entry and (entry[0] or entry[1]))

    def _chain_is_promise(self, tokens, start, call_index):
        """Whether the call at ``call_index`` is already known to produce a promise.

        A ``.then(``/``.catch(``/``.finally(`` call is a promise by construction, and so is a call
        whose receiver resolves to a promise-typed declaration, which is how a chain keeps its
        promise kind across the scanner's statement-local view.
        """
        base, members = _callee(tokens, start, call_index)
        if members and members[-1] in _CHAIN_METHODS:
            return True
        return self._is_promise_declaration(base) if base is not None else False

    # -- the walk --------------------------------------------------------------------------

    def scan(self):
        tokens = self.tokens
        self._stack = [_Frame("module", False, -1, None)]
        self._literal_braces = 0
        self._buffer = []
        self._depth = 0
        self._brackets = []
        self._try_awaits = []
        self._json_pending = []
        self._last_try = None
        self._recent = []
        for index, token in enumerate(tokens):
            if token.value == "{" and token.kind in ("punct", "other"):
                if self._is_literal_brace():
                    self._buffer.append(index)
                    self._literal_braces += 1
                    continue
                # The buffered header is what identifies the block, so it is taken before the
                # statement is flushed; `_open` consumes it and clears the buffer.
                header = self._buffer
                self._flush(index)
                self._open(index, header)
                continue
            if token.value == "}" and token.kind in ("punct", "other"):
                if self._literal_braces:
                    self._literal_braces -= 1
                    self._buffer.append(index)
                    continue
                self._flush(index)
                if len(self._stack) == 1:
                    raise _Unbalanced("unbalanced closing brace")
                self._stack.pop()
                continue
            if token.value == ";" and token.kind == "punct":
                self._flush(index)
                continue
            if (
                token.kind == "name"
                and token.value in _STATEMENT_STARTERS
                and self._starts_statement(index)
            ):
                self._flush(index)
            self._buffer.append(index)
            # Braces are consumed by the two branches above - a block opens a frame, a literal
            # brace is buffered with its partner - so only parentheses and brackets are tracked
            # here, and depth is read from the stack rather than counted.
            if token.value in ("(", "["):
                self._brackets.append(token.value)
            elif token.value in (")", "]"):
                if not self._brackets or self._brackets.pop() != _BRACKET_PAIRS[token.value]:
                    raise _Unbalanced("unbalanced closing bracket")
        self._flush(len(tokens))
        if len(self._stack) != 1:
            raise _Unbalanced("unbalanced opening brace")
        self._resolve_try_awaits()
        self.findings.sort(key=lambda item: (item["line"], item["column"], item["family"]))
        return self.findings

    def _is_literal_brace(self):
        if not self._buffer:
            return False
        token = self.tokens[self._buffer[-1]]
        if token.kind == "punct":
            return token.value in _LITERAL_BRACE_PUNCT
        if token.kind == "name":
            return token.value in _LITERAL_BRACE_NAME
        return False

    def _starts_statement(self, index):
        """Whether a statement keyword at ``index`` begins a new statement (ASI aware)."""
        if not self._buffer:
            return True
        previous = self.tokens[self._buffer[-1]]
        if previous.value == ";":
            return True
        if previous.value in ("{", "}"):
            return True
        if previous.kind == "name" and previous.value in _STATEMENT_STARTERS:
            return True
        # Inside a parenthesis or a bracket a statement keyword is a modifier, not a new
        # statement; at the top level only a line break starts one.
        if self._brackets or self.tokens[index].line == previous.line:
            return False
        return self.tokens[index].line > previous.line

    def _open(self, index, header):
        statement = [self.tokens[i] for i in header]
        kind, async_fn, loop_elements, is_catch = self._classify(statement, index)
        frame = _Frame(kind, async_fn, index, self.tokens[index])
        if kind == "try":
            self._last_try = frame
        elif is_catch and self._last_try is not None:
            # `catch` is a sibling of `try`, not a child: the try frame has already been popped by
            # the time the catch block opens, so the link is made through the scanner, not the
            # frame stack.
            self._last_try.has_catch = True
        frame.loop_elements = loop_elements
        if kind in ("function", "class"):
            for name, _is_var in self._parameters(statement):
                frame.bindings[name] = "param"
        self._stack.append(frame)

    def _classify(self, statement, index):
        """Name the block that opens at ``index`` from the header statement in front of it.

        The header is whatever the walk buffered before the brace, so the shape of that statement
        is what identifies the block: a trailing ``)`` is a control header or a signature, a
        trailing ``=>`` is an arrow function, and a bare keyword is a clause.
        """
        if not statement:
            return "block", False, 0, False
        last = statement[-1]
        if last.value == ")":
            opener = _open(statement, len(statement) - 1)
            word = statement[opener - 1] if opener else None
            if word is not None and word.kind == "name" and word.value in _CONTROL_HEADERS:
                if word.value == "for":
                    return "control", False, self._for_elements(statement, opener), False
                if word.value == "while":
                    return "control", False, self._loop_elements(statement, opener), False
                return "control", False, 0, word.value == "catch"
            if any(token.value in ("function", "=>") for token in statement):
                return "function", _signature_is_async(statement), 0, False
            return "block", False, 0, False
        if last.value == "=>":
            return "function", _signature_is_async(statement), 0, False
        if len(statement) == 1 and last.kind == "name":
            if last.value in ("try", "else", "finally", "do"):
                return last.value, False, 0, False
            if last.value == "class":
                return "class", False, 0, False
        if any(token.kind == "name" and token.value == "class" for token in statement):
            return "class", False, 0, False
        return "block", False, 0, False

    def _for_elements(self, statement, opener):
        """Element count of the iterable of a ``for`` header, or 0 when it is not literal."""
        closer = _match(statement, opener, "(", ")")
        if closer is None or closer != len(statement) - 1:
            return 0
        start = opener + 1
        if (
            start < len(statement)
            and statement[start].kind == "name"
            and statement[start].value in ("await",)
        ):
            start += 1
        depth = 0
        for index in range(start, closer):
            value = statement[index].value
            if value in _OPENERS:
                depth += 1
            elif value in _CLOSERS:
                depth -= 1
            elif depth == 0 and statement[index].kind == "name" and value in ("of", "in"):
                return _literal_collection_size(statement, index + 1, closer)
            elif depth == 0 and value == ";":
                return 0
        return 0

    def _loop_elements(self, statement, opener):
        closer = _match(statement, opener, "(", ")")
        if closer is None or closer != len(statement) - 1:
            return 0
        return _literal_collection_size(statement, opener + 1, closer)

    def _parameters(self, statement):
        """``(name, is_var)`` for the parameters of the signature in ``statement``."""
        if not any(token.value == "function" or token.value == "=>" for token in statement):
            return []
        for index, token in enumerate(statement):
            if token.value != "(":
                continue
            closer = _match(statement, index, "(", ")")
            if closer is None:
                continue
            previous = statement[index - 1] if index else None
            is_signature = (
                previous is not None
                and previous.kind == "name"
                and previous.value in ("function", "get", "set", "async")
            ) or (closer + 1 < len(statement) and statement[closer + 1].value == "=>")
            if not is_signature:
                continue
            names = []
            depth = 0
            for cursor in range(index + 1, closer):
                value = statement[cursor].value
                if value in _OPENERS:
                    depth += 1
                elif value in _CLOSERS:
                    depth -= 1
                elif (
                    depth == 0
                    and statement[cursor].kind == "name"
                    and (
                        cursor + 1 < closer and statement[cursor + 1].value in (":", "?", "=", ",")
                    )
                ):
                    names.append((statement[cursor].value, False))
            return names
        return []

    # -- statement dispatch ----------------------------------------------------------------

    def _flush(self, index):
        if not self._buffer:
            return
        tokens = self.tokens
        start, end = self._buffer[0], self._buffer[-1]
        statement = [tokens[i] for i in self._buffer]
        frame = self._stack[-1]
        self._resolve_pending(statement)
        self._recent.append(statement)
        self._recent = self._recent[-4:]
        self._declares(statement, frame)
        self._assigns(statement, frame)
        self._promise_families(statement, frame)
        self._awaits(statement, frame)
        self._equality(statement)
        self._eval_and_timers(statement)
        self._child_process(statement)
        self._credentials(statement)
        self._prototype_pollution(statement)
        self._html_sinks(statement)
        self._json_parse(statement, frame)
        self._path_join(statement)
        if self.language == "typescript":
            self._any_escape(statement, frame)
        if frame.kind == "module":
            self._module_statement(statement, frame)
        del start, end, index
        self._buffer = []

    def _resolve_pending(self, statement):
        """Decide ``unsafe-json-parse`` candidates created by the previous statement."""
        if not self._json_pending:
            return
        words = {token.value for token in statement}
        guarded = any(word in _GUARD_WORDS for word in words)
        pending, self._json_pending = self._json_pending, []
        for token, target, is_any in pending:
            if guarded:
                continue
            if is_any:
                self._add(
                    "unsafe-json-parse",
                    token,
                    "`JSON.parse` result is bound to `%s` with no shape check before use" % target,
                )
            else:
                self._add(
                    "unsafe-json-parse",
                    token,
                    "`JSON.parse` of unvalidated input is used without a shape check",
                )

    def _resolve_try_awaits(self):
        for frame, token in self._try_awaits:
            if frame.has_catch:
                self._add(
                    "redundant-await-in-try",
                    token,
                    "`return await` inside a `try` that has a `catch`: the surrounding catch sees "
                    "the rejection, so the `await` is redundant",
                )

    def _declares(self, statement, frame):
        for index, token in enumerate(statement):
            if token.kind != "name" or token.value not in _DECLARATION_KEYWORDS:
                continue
            if not self._is_declaration_keyword(statement, index):
                continue
            is_var = token.value == "var"
            for name, name_token, initialiser in self._declarators(statement, index):
                self._bind(frame, name, is_var, name_token)
                if initialiser is not None:
                    frame.state[name] = {
                        "key": self._key(statement, initialiser[0], initialiser[1]),
                        "assignments": 1,
                        "token": name_token,
                    }
            if is_var:
                self._var_hoist(statement, index, frame)

    def _var_hoist(self, statement, index, frame):
        for name, name_token, _initialiser in self._declarators(statement, index):
            for outer in reversed(self._stack[:-1]):
                if outer.kind in ("function", "arrow", "method") and name in outer.bindings:
                    self._add(
                        "var-redeclaration",
                        name_token,
                        "`var %s` inside a function body is hoisted and shadows the binding already "
                        "in scope" % name,
                    )
                    break

    def _is_declaration_keyword(self, statement, index):
        if index == 0:
            return True
        previous = statement[index - 1]
        return previous.value in (";", "{", "}", "export", "default", ",") or previous.value == ";"

    def _declarators(self, statement, index):
        """``(name, token, initialiser_range)`` for each declarator of a declaration keyword."""
        found = []
        depth = 0
        cursor = index + 1
        expect_name = True
        while cursor < len(statement):
            token = statement[cursor]
            if token.value in _OPENERS:
                if expect_name and token.value in ("[", "{"):
                    depth += 1
                    cursor += 1
                    continue
                depth += 1
            elif token.value in _CLOSERS:
                depth -= 1
            elif depth == 0 and token.value == "=":
                start = cursor + 1
                end = self._value_end(statement, start)
                cursor = end
                if found and found[-1][2] is None:
                    found[-1] = (found[-1][0], found[-1][1], (start, end))
                expect_name = True
                continue
            elif depth == 0 and token.value == ",":
                expect_name = True
                cursor += 1
                continue
            elif (
                depth == 0
                and token.kind == "name"
                and expect_name
                and (cursor + 1 >= len(statement) or statement[cursor + 1].value in (",", "=", ";"))
            ):
                found.append((token.value, token, None))
                expect_name = False
            cursor += 1
        return found

    def _value_end(self, statement, start):
        depth = 0
        for index in range(start, len(statement)):
            value = statement[index].value
            if value in _OPENERS:
                depth += 1
            elif value in _CLOSERS:
                if depth == 0:
                    return index
                depth -= 1
            elif depth == 0 and value in (";", ","):
                return index
        return len(statement)

    def _key(self, statement, start, end):
        return " ".join(token.key() for token in statement[start:end])

    def _bind(self, frame, name, is_var, token):
        if not is_var:
            frame.bindings[name] = token.value
            return
        existing = frame.bindings.get(name)
        if existing == "var":
            self._add(
                "var-redeclaration",
                token,
                "`var %s` is already bound by `var` in the same scope" % name,
            )
        else:
            for outer in reversed(self._stack[:-1]):
                if name in outer.bindings:
                    self._add(
                        "var-redeclaration",
                        token,
                        "`var %s` shadows the existing `%s` binding of the same name"
                        % (name, outer.bindings[name]),
                    )
                    break
        frame.bindings[name] = "var"

    def _assigns(self, statement, frame):
        for index, token in enumerate(statement):
            if token.kind != "punct" or token.value not in _ASSIGNMENT_OPERATORS:
                continue
            if index == 0 or statement[index - 1].kind != "name":
                continue
            if index > 1 and statement[index - 2].value in (".", "?."):
                continue
            name = statement[index - 1].value
            entry = frame.state.get(name)
            if entry is None:
                continue
            end = self._value_end(statement, index + 1)
            key = self._key(statement, index + 1, end)
            if (
                entry["assignments"] >= 1
                and entry["key"] == key
                and token.line != entry["token"].line
            ):
                self._add(
                    "needless-mutation",
                    statement[index - 1],
                    "`%s` is re-assigned the expression it was declared with" % name,
                )
            entry["key"] = key
            entry["assignments"] += 1

    # -- async families --------------------------------------------------------------------

    def _awaits(self, statement, frame):
        del frame
        for index, token in enumerate(statement):
            if token.value != "await" or index + 1 >= len(statement):
                continue
            loop = self._enclosing_loop(self._buffer[0] + index)
            if loop is not None:
                self._add(
                    "unbounded-sequential-await",
                    token,
                    "`await` resolves one element at a time over a %d-element literal; the body is "
                    "independent per element, so the calls could have been composed"
                    % loop.loop_elements,
                )
            if index == 0 or statement[index - 1].value != "return":
                continue
            for candidate in reversed(self._stack):
                if candidate.is_try:
                    self._try_awaits.append((candidate, token))
                    break

    def _enclosing_loop(self, position):
        for frame in reversed(self._stack):
            if frame.loop_elements > 1 and frame.open_index < position:
                return frame
        return None

    def _promise_families(self, statement, frame):
        enclosing = self._enclosing_async()
        is_statement = self._is_expression_statement(statement)
        for index, token in enumerate(statement):
            if not _is_call_at(statement, index):
                continue
            base, members = _callee(statement, 0, index)
            leaf = members[-1] if members else None
            if leaf in _CHILD_PROCESS_CALLS or (base is not None and base in _CHILD_PROCESS_CALLS):
                continue
            awaited = self._is_awaited(statement, index)
            voided = index > 0 and statement[index - 1].value == "void"
            chain = self._chain_is_promise(statement, 0, index)
            if leaf == "then" and not awaited and not voided:
                if not self._has_catch_after(statement, index):
                    self._add(
                        "unhandled-rejection",
                        token,
                        "a `.then(` chain with no `catch` downstream: its rejection is unobserved",
                    )
                continue
            if not chain or awaited or voided:
                if (
                    enclosing is not None
                    and not awaited
                    and not is_statement
                    and base is not None
                    and self._is_async_declaration(base)
                    and not self._is_inside_function(statement, index)
                ):
                    self._add(
                        "missing-await",
                        token,
                        "an async call inside another async function is used without `await`, so "
                        "its rejection escapes the caller's control flow",
                    )
                continue
            if not is_statement:
                continue
            self._add(
                "floating-promise",
                token,
                "a promise-producing expression used as a bare statement: neither awaited, nor "
                "`void`ed, nor given a `.catch(`, so a rejection is unobserved",
            )
        del frame

    def _is_inside_function(self, statement, index):
        for cursor in range(index + 1, len(statement)):
            if statement[cursor].value == "=>":
                return True
            if statement[cursor].value == "function":
                return True
        return False

    def _is_awaited(self, statement, index):
        if index and statement[index - 1].value == "await":
            return True
        for offset in (1, 2):
            if index - offset >= 0 and statement[index - offset].value in ("return", "yield", "=>"):
                return True
            if (
                index - offset >= 0
                and statement[index - offset].value == "("
                and (index - offset - 1 >= 0 and statement[index - offset - 1].value == "await")
            ):
                return True
        return False

    def _has_catch_after(self, statement, index):
        for cursor in range(index, len(statement)):
            if statement[cursor].kind == "name" and statement[cursor].value == "catch":
                return True
        return False

    def _is_expression_statement(self, statement):
        return not any(token.value == "=" for token in statement) or not any(
            token.kind == "name" and token.value in _DECLARATION_KEYWORDS for token in statement[:1]
        )

    def _enclosing_async(self):
        for frame in reversed(self._stack):
            if frame.kind in ("function", "arrow", "method"):
                return frame
        return None

    # -- security families -----------------------------------------------------------------

    def _eval_and_timers(self, statement):
        for index, token in enumerate(statement):
            if token.kind != "name" or index + 2 >= len(statement):
                continue
            if (
                token.value == "eval"
                and _is_call_at(statement, index)
                and statement[index + 2].kind != "literal"
            ):
                self._add(
                    "dynamic-eval",
                    token,
                    "`eval` compiles a value the tool never inspects",
                )
            elif (
                token.value == "Function"
                and index
                and statement[index - 1].value == "new"
                and (statement[index + 1].value == "(")
            ):
                self._add(
                    "dynamic-eval",
                    token,
                    "`new Function` builds a function from a body assembled at run time",
                )
            elif token.value in _TIMER_CALLS and _is_call_at(statement, index):
                argument = statement[index + 2]
                if argument.kind == "literal" and argument.value in ("string", "template"):
                    self._add(
                        "dynamic-eval",
                        token,
                        "`%s` is given a string body, which the engine compiles as code"
                        % token.value,
                    )

    def _child_process(self, statement):
        for index, token in enumerate(statement):
            if token.kind != "name" or index + 1 >= len(statement):
                continue
            if statement[index + 1].value != "(":
                continue
            base, members = _callee(statement, 0, index)
            leaf = members[-1] if members else base
            if leaf not in _CHILD_PROCESS_CALLS:
                continue
            closer = _match(statement, index + 1, "(", ")")
            if closer is None or not self._shell_true(statement, index + 1, closer):
                continue
            if not self._built_command(statement, index + 2, closer):
                continue
            self._add(
                "command-injection",
                token,
                "a `child_process` call with `shell: true` runs a command built from a "
                "non-literal segment, so a metacharacter in the value becomes a second command",
            )

    def _shell_true(self, statement, open_index, closer):
        depth = 0
        for index in range(open_index + 1, closer):
            value = statement[index].value
            if value in _OPENERS:
                depth += 1
            elif value in _CLOSERS:
                depth -= 1
            elif (
                depth == 1 and statement[index].kind == "name" and statement[index].value == "shell"
            ):
                after = statement[index + 1] if index + 1 < closer else None
                if after is not None and after.value == ":" and index + 2 < closer:
                    value_token = statement[index + 2]
                    if value_token.kind == "name" and value_token.value == "true":
                        return True
        return False

    def _built_command(self, statement, start, closer):
        """True when the first argument is a template literal or a concatenation, non-literal."""
        index = start
        while index < closer and statement[index].value in ("...",):
            index += 1
        if index >= closer:
            return False
        head = statement[index]
        if head.kind == "literal" and head.value == "template":
            return "${" in head.raw
        if head.kind == "literal":
            return False
        if head.value == "+":
            return True
        if head.kind == "name" and head.value == "String":
            return True
        if head.kind == "name" and head.value in ("`",):
            return True
        return False

    def _credentials(self, statement):
        """A string literal assigned to a credential-shaped binding or property."""
        for index, token in enumerate(statement):
            if token.kind != "literal" or token.value not in ("string", "template"):
                continue
            if token.value == "template" and "${" in token.raw:
                continue  # an interpolated template is assembled at run time, not hardcoded
            if index == 0 or statement[index - 1].value not in ("=", ":"):
                continue
            name, position = self._assigned_name(statement, index - 1)
            if name is None or not _CREDENTIAL.search(name):
                continue
            self._add(
                "hardcoded-credential",
                statement[position],
                "a string literal is assigned to `%s`: the secret ships in the source instead of "
                "coming from the environment" % name,
            )

    def _assigned_name(self, statement, index):
        """``(name, position)`` of the binding or property the operator at ``index`` assigns.

        Both `const token = "..."` and `config.token = "..."` are credential shapes, so a member
        assignment counts too; the reported position is the property name either way.
        """
        if index and statement[index - 1].kind == "name":
            return statement[index - 1].value, index - 1
        return None, index

    def _prototype_pollution(self, statement):
        for index, token in enumerate(statement):
            if (
                token.kind == "name"
                and token.value == "__proto__"
                and self._is_target(statement, index)
            ):
                self._add(
                    "prototype-pollution",
                    token,
                    "`__proto__` is assigned: the write reaches an object's prototype and every "
                    "object that inherits from it",
                )
                continue
            if (
                token.kind == "name"
                and token.value == "constructor"
                and self._prototype_chain_target(statement, index)
            ):
                self._add(
                    "prototype-pollution",
                    token,
                    "`constructor.prototype` is assigned: the write reaches the prototype shared "
                    "by every instance",
                )
                continue
            if token.kind == "name" and token.value == "[" and self._dynamic_key(statement, index):
                self._add(
                    "prototype-pollution",
                    token,
                    "a dynamic key is written into an object literal's prototype position",
                )

    def _prototype_chain_target(self, statement, index):
        """True for ``constructor.prototype.<key> = ...``, which reaches every instance."""
        if index + 3 >= len(statement):
            return False
        if statement[index + 1].value != "." or statement[index + 2].value != "prototype":
            return False
        if statement[index + 3].value != "." or statement[index + 4].kind != "name":
            return False
        return index + 5 < len(statement) and statement[index + 5].value in _ASSIGNMENT_OPERATORS

    def _is_target(self, statement, index):
        """True when the name at ``index`` is the left-hand side of an assignment."""
        if index + 1 >= len(statement):
            return False
        if statement[index + 1].value not in _ASSIGNMENT_OPERATORS:
            return False
        if index >= 1 and statement[index - 1].value in (".", "?."):
            return True  # `obj.__proto__ = ...`
        if index >= 1 and statement[index - 1].value == "]":
            return True  # `obj[key] = ...`
        return index == 0

    def _dynamic_key(self, statement, index):
        if index == 0 or statement[index - 1].value not in (".", "?."):
            return False
        closer = _match(statement, index, "[", "]")
        if closer is None or closer + 1 >= len(statement):
            return False
        if statement[closer + 1].value not in _ASSIGNMENT_OPERATORS:
            return False
        key = statement[index + 1 : closer]
        if not key:
            return True
        return not all(token.kind == "literal" for token in key)

    def _html_sinks(self, statement):
        for index, token in enumerate(statement):
            if token.kind != "name" or index + 1 >= len(statement):
                continue
            if token.value in _HTML_PROPERTIES and statement[index + 1].value == "=":
                if self._is_unsafe_value(statement, index + 2):
                    self._add(
                        "unsafe-html",
                        token,
                        "`%s` is assigned a value the document is about to parse as markup"
                        % token.value,
                    )
            elif token.value == "insertAdjacentHTML" and statement[index + 1].value == "(":
                if self._is_unsafe_value(statement, index + 2):
                    self._add(
                        "unsafe-html",
                        token,
                        "`insertAdjacentHTML` is given a value the document is about to parse as "
                        "markup",
                    )
            elif (
                token.value == "write"
                and index
                and statement[index - 1].value == "."
                and (
                    index > 1
                    and statement[index - 2].kind == "name"
                    and statement[index - 2].value in ("document", "globalThis")
                )
            ):
                if self._is_unsafe_value(statement, index + 1):
                    self._add(
                        "unsafe-html",
                        token,
                        "`document.write` is given a value the document is about to parse as "
                        "markup",
                    )

    def _is_unsafe_value(self, statement, start):
        if start >= len(statement):
            return False
        head = statement[start]
        if head.kind == "literal":
            if head.value == "template":
                return "${" in head.raw
            return False  # a plain string literal cannot carry markup the document did not author
        if head.value in ("typeof", "void"):
            return False
        return True

    def _json_parse(self, statement, frame):
        for index, token in enumerate(statement):
            if token.kind != "name" or token.value != "JSON":
                continue
            if (
                index + 3 >= len(statement)
                or statement[index + 1].value != "."
                or statement[index + 2].value != "parse"
                or statement[index + 3].value != "("
            ):
                continue
            closer = _match(statement, index + 3, "(", ")")
            if closer is None:
                continue
            after = statement[closer + 1 :]
            if any(part.value in ("typeof", "instanceof") for part in after):
                continue
            if any(part.kind == "name" and part.value == "in" for part in after):
                continue
            if (
                index
                and statement[index - 1].kind == "name"
                and statement[index - 1].value
                in (
                    "await",
                    "yield",
                )
            ):
                continue
            bound = None
            if (
                index
                and statement[index - 1].value in ("=", ":")
                and index >= 2
                and statement[index - 2].kind == "name"
            ):
                bound = statement[index - 2].value
            elif (
                index
                and statement[index - 1].kind == "name"
                and statement[index - 1].value
                in (
                    "return",
                    "export",
                    "throw",
                )
            ):
                bound = None
            elif after and after[0].value in (",", ")"):
                continue
            if (
                after
                and after[0].value == "."
                and len(after) > 1
                and after[1].value
                in (
                    "forEach",
                    "map",
                    "filter",
                    "reduce",
                )
            ):
                continue
            if self._is_declaration_keyword(statement, 0) and bound is not None:
                self._json_pending.append((statement[index + 2], bound, False))
                continue
            self._add(
                "unsafe-json-parse",
                statement[index + 2],
                "`JSON.parse` of unvalidated input reaches the caller with no shape check",
            )
        del frame

    def _path_join(self, statement):
        for index, token in enumerate(statement):
            if token.kind != "name" or not _PATH_RECEIVER.match(token.value):
                continue
            for cursor in range(index + 1, len(statement)):
                value = statement[cursor].value
                if value == "." and statement[cursor + 1].value in _PATH_METHODS:
                    if cursor + 2 < len(statement) and statement[cursor + 2].value == "(":
                        self._path_argument(statement, cursor + 2)
                    break
                if value == "(":
                    break

    def _path_argument(self, statement, open_index):
        closer = _match(statement, open_index, "(", ")")
        if closer is None or closer == open_index + 1:
            return
        if self._is_unsafe_value(statement, open_index + 1):
            self._add(
                "unvalidated-path-join",
                statement[open_index],
                "a path is built from a non-literal segment: a `..` or separator in that value "
                "escapes the intended directory",
            )

    # -- lint confirmation and TypeScript ---------------------------------------------------

    def _equality(self, statement):
        for index, token in enumerate(statement):
            if token.kind != "punct" or token.value not in ("==", "!="):
                continue
            left = self._operand_kind(statement, index - 1, -1)
            right = self._operand_kind(statement, index + 1, 1)
            if left is None or right is None or left == right:
                continue
            # `x == null` and `x != null` are the idiomatic null check and the one form of loose
            # equality that is correct: they match null and undefined together on purpose. The
            # ESLint rule suspects them; the scanner confirms nothing, so the family does not fire.
            if left in ("null", "undefined", "NaN") or right in ("null", "undefined", "NaN"):
                continue
            self._add(
                "loose-equality",
                token,
                "`%s` compares operands of different apparent kinds (%s and %s): the comparison "
                "coerces one of them" % (token.value, left, right),
            )

    def _operand_kind(self, statement, start, direction):
        index = start
        while index >= 0 and index < len(statement):
            token = statement[index]
            if token.kind == "name":
                if token.value in ("null", "undefined", "NaN"):
                    return token.value
                if token.value == "typeof":
                    return "typeof"
                if token.value in ("true", "false"):
                    return "boolean"
                return "value"
            if token.kind == "literal":
                return "string" if token.value in ("string", "template") else "regex"
            if token.kind == "number":
                return "number"
            if token.value == ")":
                opener = _open(statement, index)
                if opener is None:
                    return None
                index = opener - 1
                if index >= 0 and statement[index].value in (".", "?."):
                    index -= 1
                continue
            if token.value == "]":
                opener = _open(statement, index)
                if opener is None:
                    return None
                index = opener - 1
                continue
            if token.value in ("-", "+") and direction < 0 and index > 0:
                index -= 1
                continue
            return None
        return None

    def _any_escape(self, statement, frame):
        for index, token in enumerate(statement):
            if token.kind != "name" or token.value != "any":
                continue
            if self._is_public_any(statement, index):
                self._add(
                    "any-escape",
                    token,
                    "an explicit `any` sits in a public position: the value crosses the type "
                    "boundary unchecked",
                )
        self._untyped_json_any(statement, frame)

    def _is_public_any(self, statement, index):
        if index == 0 or index + 1 >= len(statement):
            return False
        after = statement[index + 1]
        if after.value not in (
            ";",
            ")",
            ",",
            "]",
            "}",
            "=",
            "|",
            "&",
            ">",
            "?",
            "=>",
            "{",
            "extends",
        ):
            return False
        before = statement[index - 1]
        if before.kind == "name" and before.value in ("as", "satisfies", "is", "extends"):
            return any(part.value == "export" for part in statement[:index])
        if before.value in ("<", ",", "|", "&"):
            return any(part.kind == "name" and part.value in _SIGNATURE_WORDS for part in statement)
        if before.value != ":":
            return False
        # A parameter or return annotation inside any function signature is a public position.
        # A local `const x: any = ...` is not: `tsc` checks it at the declaration and the value
        # never crosses a boundary, so it is not what this family is about. The signature keyword
        # may already have been flushed as the block's header, so `export` is not required.
        if after.value == "=" and any(
            part.kind == "name" and part.value in _DECLARATION_KEYWORDS for part in statement
        ):
            return False
        return any(part.kind == "name" and part.value in _SIGNATURE_WORDS for part in statement)

    def _untyped_json_any(self, statement, frame):
        for index, token in enumerate(statement):
            if token.kind != "name" or token.value != "JSON":
                continue
            if (
                index + 3 >= len(statement)
                or statement[index + 1].value != "."
                or statement[index + 2].value != "parse"
                or statement[index + 3].value != "("
            ):
                continue
            if not self._is_declaration_keyword(statement, 0) or index < 3:
                continue
            if statement[index - 1].value not in ("=", ":") or statement[index - 2].kind != "name":
                continue
            target = statement[index - 2].value
            if statement[index - 1].value == ":":
                continue
            if len(self._recent) > 3:
                return
            for earlier in self._recent[-3:]:
                if earlier[0].kind == "name" and earlier[0].value in ("return", "export"):
                    self._add(
                        "any-escape",
                        statement[index + 2],
                        "an untyped `JSON.parse` result is returned as `%s`, so the value leaves "
                        "the module untyped" % target,
                    )
                    break
                if any(
                    part.kind == "name" and part.value in ("return", "export") for part in earlier
                ) and any(part.kind == "name" and part.value == target for part in earlier):
                    self._add(
                        "any-escape",
                        statement[index + 2],
                        "an untyped `JSON.parse` result bound to `%s` is returned or exported"
                        % target,
                    )
                    break
        del frame

    # -- module level -----------------------------------------------------------------------

    def _module_statement(self, statement, frame):
        head = statement[0]
        if head.kind == "name" and head.value in (
            "import",
            "function",
            "class",
            "abstract",
            "interface",
            "type",
            "enum",
            "declare",
        ):
            return
        if head.kind == "name" and head.value in _DECLARATION_KEYWORDS:
            return
        if head.value in ("{", "(", "["):
            return  # a literal or a parenthesised expression; an IIFE is handled below
        if head.kind == "name" and head.value == "export":
            if any(part.value == "{" for part in statement[1:]) and not any(
                part.value == "=" for part in statement[1:]
            ):
                return
            for part in statement[1:]:
                if part.kind == "name" and part.value in ("let", "var"):
                    for name, name_token, _initialiser in self._declarators(statement, 1):
                        self._add(
                            "exported-mutable-binding",
                            name_token,
                            "`export %s %s` shares one live binding with every importer, so a "
                            "later mutation is visible outside this module" % (part.value, name),
                        )
                    return
            if any(
                part.kind == "name" and part.value in ("process", "document", "window")
                for part in statement
            ):
                return
            return
        for index, token in enumerate(statement):
            if (
                token.kind == "name"
                and token.value == "export"
                and any(part.value in ("let", "var") for part in statement[index:])
            ):
                for name, name_token, _initialiser in self._declarators(statement, index + 1):
                    self._add(
                        "exported-mutable-binding",
                        name_token,
                        "`export %s %s` shares one live binding with every importer"
                        % (statement[index + 1].value, name),
                    )
        if (
            head.kind == "name"
            and head.value == "async"
            and statement[1:2]
            and statement[1].value == "("
        ):
            if self._side_effect_call(
                statement,
                1,
                "an async IIFE at module scope starts work as soon as the module is imported",
            ):
                return
        if head.value == "(":
            if self._side_effect_call(
                statement, 0, "a module-scope IIFE runs work as soon as the module is imported"
            ):
                return
        for index, token in enumerate(statement):
            if token.kind != "name" or not _is_call_at(statement, index):
                continue
            base, members = _callee(statement, 0, index)
            leaf = members[-1] if members else base
            if leaf in _TIMER_CALLS:
                self._add(
                    "import-time-side-effect",
                    token,
                    "a timer is registered at module scope, so the callback starts as soon as the "
                    "module is imported",
                )
                return
            if leaf in _LISTENER_METHODS and not self._is_inside_function(statement, index):
                self._add(
                    "import-time-side-effect",
                    token,
                    "an event listener is registered at module scope, so the callback runs as "
                    "soon as the event fires whether or not anything asked for it",
                )
                return
            if base == "process" and leaf == "on":
                self._add(
                    "import-time-side-effect",
                    token,
                    "a process handler is registered at module scope, so the module takes over "
                    "that signal for the whole process",
                )
                return
        del frame

    def _side_effect_call(self, statement, open_index, explanation):
        closer = _match(statement, open_index, "(", ")")
        if closer is None or closer != len(statement) - 1:
            return True
        inner = statement[open_index + 1 : closer]
        if not inner:
            return False
        head = inner[0]
        if head.kind == "name" and head.value == "async":
            self._add("import-time-side-effect", statement[open_index], explanation)
            return True
        base, members = _callee(inner, 0, len(inner))
        if base is not None and self._is_promise_declaration(base):
            self._add("import-time-side-effect", statement[open_index], explanation)
        return True

    def _add(self, family, token, explanation):
        self.findings.append(
            {
                "check_id": "%s.context.%s" % (self.language, family),
                "path": self.path,
                "line": token.line,
                "column": token.column,
                "family": family,
                "severity": _SEVERITY[family],
                "explanation": explanation,
            }
        )


def _at(tokens, index, name):
    return index >= 0 and index < len(tokens) and tokens[index].value == name


def _suffix(path):
    return os.path.splitext(path)[1].lower()


def scan_text(path, source):
    """Findings for one file; raises ``_Unbalanced`` when the file cannot be walked."""
    scanner = _Scanner(path, source)
    return scanner.scan()


def scan(root, paths):
    scanned, findings, failures = [], [], []
    for relative in paths:
        suffix = _suffix(relative)
        if suffix not in _LANGUAGE:
            failures.append({"path": relative, "reason": "unsupported-extension"})
            continue
        try:
            with open(os.path.join(root, relative), "rb") as handle:
                source = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            failures.append({"path": relative, "reason": type(error).__name__})
            continue
        try:
            found = scan_text(relative, source)
        except _Unbalanced as error:
            failures.append({"path": relative, "reason": str(error)})
            continue
        scanned.append(relative)
        findings.extend(found)
    findings.sort(key=lambda item: (item["path"], item["line"], item["column"], item["family"]))
    languages = sorted({_LANGUAGE[_suffix(path)] for path in scanned})
    return {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "language": languages[0] if len(languages) == 1 else "mixed",
        "scanned": scanned,
        "findings": findings,
        "parse_failures": failures,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_js_scan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv[1:])
    result = scan(args.root, args.paths)
    parent = os.path.dirname(args.output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True, separators=(",", ":"))
    # The plan declares `findings=(1,)`, so a scan that reported findings must exit 1 and the host
    # parser treats exit 0 *with* findings as a broken scan ("exit 0 but violations were
    # reported"). Exiting 0 here made every candidate with a real finding read as `missing`.
    if result["parse_failures"]:
        return 2
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
