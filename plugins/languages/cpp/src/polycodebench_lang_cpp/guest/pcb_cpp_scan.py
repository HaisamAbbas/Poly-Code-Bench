"""Context-aware C++ idiom scanner (pure stdlib; ships inside the images).

C++'s most tokenised idioms are only defects *in context*, which is exactly what an automatic
verdict must not be:

* ``new`` / ``delete`` - a manual allocation is a violation only when nothing releases it. Wrapped
  immediately in ``std::unique_ptr``/``make_unique``/``make_shared`` it is ``benign_in_context``,
  and a class that declares a destructor is releasing it by construction.
* a raw pointer parameter - a non-owning ``T*`` is legal, idiomatic C++ and is **never** a
  violation; it is recorded as ``benign_in_context`` so the evidence shows it was considered.
* ``std::move`` on a ``const`` object, a container copy that value semantics already forbid, a
  pass by value of a container, an index loop where a range loop belongs, ``+=`` on a string inside
  a loop, a destructor that throws and a ``return local;`` that leans on implicit move: those are
  defects on their own.

Comments, string literals and character literals are blanked first, keeping every offset, so a
finding's line and column are the line and column of the offending *token* in the real file: the
host parser matches a scanner finding against clang-tidy's or cppcheck's line for the same defect,
and a match on a shifted line would silently split one defect into two findings.

Records carry ``violation`` (counts), ``benign_in_context`` (evidence, never penalised) or
``hint``. Counting constructs *used* never earns credit: the scanner only reports defects;
opportunity counts come from the frozen task.

Usage: python pcb_cpp_scan.py --root DIR --output FILE [--opportunities a,b] [--tags t1,t2] PATH...
Exit: 0 clean, 1 violations, 2 incomplete.
"""

import argparse
import json
import os
import re
import sys

SCANNER_VERSION = "1"
SCHEMA = "pcb-cpp-scan-v1"

# Container and string types are the ones where a copy, a pass by value or an index loop is a
# measured cost rather than a style question.
_CONTAINERS = (
    "vector",
    "map",
    "multimap",
    "unordered_map",
    "unordered_multimap",
    "set",
    "multiset",
    "unordered_set",
    "unordered_multiset",
    "deque",
    "list",
    "forward_list",
    "array",
)
_STRINGS = (
    "string",
    "wstring",
    "u8string",
    "u16string",
    "u32string",
    "string_view",
    "basic_string",
)
_CONTAINER_HEAD = "(?:std::)(" + "|".join(_CONTAINERS) + "|" + "|".join(_STRINGS) + r")\b"
_KEYWORDS = (
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "delete",
    "new",
    "operator",
    "throw",
    "case",
    "do",
)

# `std::vector<int> v`, `std::map<K, V> m`, `const std::string& s`, `std::string* owned`.
# Every gap between the type and the name must contain whitespace or a `*`, otherwise the
# backtracking engine happily splits an identifier and reports the variable's last letter.
_CONTAINER_DECL = re.compile(
    r"\b" + _CONTAINER_HEAD + r"\s*(?:<[^;{}()\[\]]*>)?(?:\s*::\s*[A-Za-z_]\w*)*"
    r"(?:\s*\*\s*|\s+[A-Za-z_]\w*\s+)?&?\s*&?\s*([A-Za-z_]\w*)\s*(?=[;={(,\[=)])"
)
_NEW = re.compile(r"\bnew\b")
_DELETE = re.compile(r"\bdelete\b")
_ARRAY_SUFFIX = re.compile(r"\s*\[\s*\]")
_OWNING_WRAP = re.compile(
    r"=\s*(?:std::)?(?:unique_ptr|shared_ptr)\s*<|=\s*(?:std::)?make_(?:unique|shared)\s*\("
)
_RAW_POINTER_PARAM = re.compile(
    r"(?:^|[(,])\s*(?:const\s+)?[A-Za-z_]\w*(?:\s*<[^()]*>)?(?:\s*::\s*[A-Za-z_]\w*)*"
    r"(?:\s+const)?\s*\*\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?=[,)=])"
)
_PARAM_LIST = re.compile(r"([A-Za-z_]\w*(?:\s*<[^(){}]*>)?(?:\s*\*|&)*)\s*\(([^(){}]*)\)")
_INDEX_LOOP = re.compile(
    r"\bfor\s*\(\s*(?:std::)?(?:size_t|ssize_t|unsigned|int|long)\s+[A-Za-z_]\w*\s*=\s*[^;()]*;\s*"
    r"[A-Za-z_]\w*\s*<\s*[\w.>\[\]:]*\.\s*size\s*\(\)"
)
_ITERATOR_LOOP = re.compile(
    r"\bfor\s*\(\s*(?:auto|[\w:<>]+)\s+([A-Za-z_]\w*)\s*=\s*([A-Za-z_][\w.>\[\]]*)\s*\.\s*begin\s*\(\s*\)"
    r"\s*;\s*\1\s*!=\s*\2\s*\.\s*end\s*\("
)
_LOOP_HEAD = re.compile(r"\b(?:for|while)\s*\(")
_STRING_ADD = re.compile(r"([A-Za-z_]\w*)\s*\+=")
_MOVE = re.compile(r"\b(?:std::)?move\s*\(\s*([A-Za-z_]\w*)\s*\)")
_CONST_DECL = re.compile(
    r"\bconst\s+(?:std::)?[A-Za-z_]\w*(?:\s*<[^;{}()\[\]]*>)?(?:\s*::\s*[A-Za-z_]\w*)*"
    r"(?:\s+const)?\s*\*?\s*&?\s*([A-Za-z_]\w*)\s*(?=[;={(,)\[])"
)
_CLASS_HEAD = re.compile(r"\b(?:class|struct)\s+([A-Za-z_]\w*)\s*(?:final\s*)?(?::[^{;]*)?\{")
_MEMBER_POINTER = re.compile(
    r"(?:^|[;{}])\s*(?:mutable\s+|static\s+)*(?:const\s+)?[A-Za-z_]\w*(?:\s*<[^;{}()]*>)?"
    r"(?:\s*::\s*[A-Za-z_]\w*)*\s*\*\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?=[;=,\[])",
    re.MULTILINE,
)
_NON_POINTER_DELETE = re.compile(
    r"^\s*(?:-?\d[\w.]*|true|false|nullptr|0x[0-9a-fA-F]+)"
    r"|^\s*[A-Za-z_]\w*\s*\([^()]*\)"
    r"|^\s*[\w.]+\s*(?:->|\.)\s*[A-Za-z_]\w*"
    r"|^\s*[A-Za-z_]\w*\s*\[[^\]]*\]"
)


def blank(text):
    """Replace comment and literal bodies with spaces, keeping every offset and newline.

    Offsets are the whole point: every rule matches the blanked text, so a reported column is the
    column of the token in the original file. A ``delete`` inside a comment is a comment, and a
    ``new`` inside a string literal is text.
    """
    out = list(text)
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            while index < length and text[index] != "\n":
                out[index] = " "
                index += 1
            continue
        if char == "/" and nxt == "*":
            depth = 1
            out[index] = out[index + 1] = " "
            index += 2
            while index < length and depth:
                if text.startswith("/*", index):
                    depth += 1
                    out[index] = out[index + 1] = " "
                    index += 2
                    continue
                if text.startswith("*/", index):
                    depth -= 1
                    out[index] = out[index + 1] = " "
                    index += 2
                    continue
                if text[index] != "\n":
                    out[index] = " "
                index += 1
            continue
        if char in "\"'":
            terminator = None
            if char == '"' and text.startswith('R"', index):
                open_paren = text.find("(", index + 2)
                if open_paren != -1:
                    delimiter = text[open_paren + 1 : open_paren + 17].split(")")[0]
                    terminator = ")" + delimiter + '"'
            out[index] = " "
            index += 1
            if terminator:
                close = text.find(terminator, index)
                stop = len(text) if close == -1 else close + len(terminator)
                while index < stop:
                    if text[index] != "\n":
                        out[index] = " "
                    index += 1
                continue
            while index < length:
                current = text[index]
                if current == "\\" and index + 1 < length:
                    if text[index + 1] != "\n":
                        out[index] = " "
                        out[index + 1] = " "
                    index += 2
                    continue
                if current == char or current == "\n":
                    if current == char:
                        out[index] = " "
                    index += 1
                    break
                out[index] = " "
                index += 1
            continue
        index += 1
    return "".join(out)


def _line_of(code, offset):
    return code.count("\n", 0, offset) + 1


def _column_of(code, offset):
    return offset - (code.rfind("\n", 0, offset) + 1) + 1


def _match_delimited(code, opener, opening, closing):
    """End offset of the block opened at ``opener``; -1 when it never closes."""
    depth = 0
    for index in range(opener, len(code)):
        char = code[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _statement_end(code, start):
    """Offset of the semicolon closing the statement at ``start``, at depth zero."""
    depth = 0
    for index in range(start, len(code)):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return index
        elif char == ";" and depth <= 0:
            return index
    return len(code)


def _is_parameter_site(code, offset):
    """True when a declaration at ``offset`` sits in a parameter list rather than in a body."""
    before = code[:offset].rstrip()
    if not before:
        return False
    if before.endswith("("):
        return True
    if before.endswith(","):
        window = before[: before.rfind(",")]
        return window.count("(") > window.count(")")
    return False


class _File:
    """Everything derived once per file, then queried by the individual rules."""

    def __init__(self, path, text):
        self.path = path
        self.code = blank(text)
        if self.code.count("{") != self.code.count("}"):
            raise ValueError("unbalanced braces")
        if self.code.count("(") != self.code.count(")"):
            raise ValueError("unbalanced parentheses")
        self.classes = []
        for match in _CLASS_HEAD.finditer(self.code):
            body_start = match.end()
            end = _match_delimited(self.code, body_start - 1, "{", "}")
            if end == -1:
                raise ValueError("unbalanced class body")
            self.classes.append((match.group(1), match.start(), body_start, end + 1))
        self.findings = []
        self.container_vars = {}
        self.string_vars = set()
        self.const_vars = set()
        self.allocations = {}
        self.new_statements = []
        self._collect_declarations()
        self._collect_allocations()

    # -- declaration tables ---------------------------------------------------------------
    def _collect_declarations(self):
        for match in _CONTAINER_DECL.finditer(self.code):
            head, name = match.group(1), match.group(2)
            if name in _KEYWORDS:
                continue
            entry = {
                "type": "std::" + head,
                "string": head in _STRINGS,
                "parameter": _is_parameter_site(self.code, match.start()),
            }
            self.container_vars.setdefault(name, entry)
            if entry["string"]:
                self.string_vars.add(name)
        for match in _CONST_DECL.finditer(self.code):
            name = match.group(1)
            if name not in _KEYWORDS:
                self.const_vars.add(name)

    def _collect_allocations(self):
        for match in _NEW.finditer(self.code):
            end = _statement_end(self.code, match.start())
            statement = self.code[match.start() : end + 1]
            target = re.search(
                r"([A-Za-z_]\w*)\s*=\s*$", self.code[max(0, match.start() - 64) : match.start()]
            )
            if target:
                self.allocations.setdefault(
                    target.group(1), "array" if "[" in statement else "scalar"
                )
            self.new_statements.append((match.start(), end, statement, target))

    def _class_of(self, offset):
        found = None
        for entry in self.classes:
            if entry[1] <= offset < entry[3] and (found is None or entry[1] > found[1]):
                found = entry
        return found

    def _parameters(self):
        """Every function-like parameter list as (span start, parameter text)."""
        for match in _PARAM_LIST.finditer(self.code):
            before = self.code[: match.start()].rstrip()
            word = re.search(r"([A-Za-z_]\w*)\s*$", before)
            if not word or word.group(1) in _KEYWORDS:
                continue
            yield match.start(2), match.group(2)

    def _loop_bodies(self):
        for match in _LOOP_HEAD.finditer(self.code):
            close = _match_delimited(self.code, match.end() - 1, "(", ")")
            if close == -1:
                continue
            cursor = close + 1
            while cursor < len(self.code) and self.code[cursor] in " \t":
                cursor += 1
            if cursor >= len(self.code):
                continue
            if self.code[cursor] == "{":
                end = _match_delimited(self.code, cursor, "{", "}")
                if end == -1:
                    continue
                yield cursor + 1, end
            else:
                yield cursor, _statement_end(self.code, cursor)

    # -- rules ----------------------------------------------------------------------------
    def _finding(
        self,
        rule,
        offset,
        end_offset,
        verdict,
        message,
        symbol="",
        confidence="high",
        evidence=None,
    ):
        self.findings.append(
            {
                "rule": rule,
                "path": self.path,
                "line": _line_of(self.code, offset),
                "end_line": _line_of(self.code, max(offset, end_offset)),
                "column": _column_of(self.code, offset),
                "symbol": symbol,
                "verdict": verdict,
                "confidence": confidence,
                "message": message,
                "evidence": evidence or {},
            }
        )

    def raw_owning_pointer(self):
        for start, end, statement, target in self.new_statements:
            symbol = target.group(1) if target else ""
            wrap = _OWNING_WRAP.search(self.code[max(0, start - 64) : end])
            if wrap:
                self._finding(
                    "raw-owning-pointer",
                    start,
                    end,
                    "benign_in_context",
                    "allocation is wrapped immediately in a smart pointer",
                    symbol,
                    evidence={"wrapper": wrap.group(0).strip()},
                )
                continue
            owner = self._class_of(start)
            if owner and re.search(
                r"~\s*" + re.escape(owner[0]) + r"\s*\(", self.code[owner[1] : owner[3]]
            ):
                self._finding(
                    "raw-owning-pointer",
                    start,
                    end,
                    "benign_in_context",
                    "class %s releases the allocation through its own destructor" % owner[0],
                    symbol,
                    evidence={"class": owner[0]},
                )
                continue
            self._finding(
                "raw-owning-pointer",
                start,
                end,
                "violation",
                "manual allocation is never released",
                symbol,
                evidence={"statement": statement.strip()[:200]},
            )

    def raw_delete_mismatch(self):
        for match in _DELETE.finditer(self.code):
            start = match.start()
            suffix = _ARRAY_SUFFIX.match(self.code[match.end() :])
            array = suffix is not None
            operand_start = match.end() + (len(suffix.group(0)) if suffix else 0)
            end = _statement_end(self.code, match.end())
            operand = self.code[operand_start:end].strip()
            symbol = re.match(r"([A-Za-z_]\w*)\s*$", operand)
            kind = self.allocations.get(symbol.group(1)) if symbol else None
            if kind is not None and (
                (array and kind == "scalar") or (not array and kind == "array")
            ):
                self._finding(
                    "raw-delete-mismatch",
                    start,
                    end,
                    "violation",
                    "delete[] on a non-array allocation"
                    if array
                    else "delete on an array allocation",
                    symbol.group(1),
                    evidence={"operand": operand[:120]},
                )
                continue
            if _NON_POINTER_DELETE.match(operand):
                self._finding(
                    "raw-delete-mismatch",
                    start,
                    end,
                    "violation",
                    "delete applied to something that is not a pointer",
                    symbol.group(1) if symbol else "",
                    confidence="medium",
                    evidence={"operand": operand[:120]},
                )

    def rule_of_five_missing(self):
        for name, _start, body_start, body_end in self.classes:
            body = self.code[body_start:body_end]
            dtor = re.search(r"~\s*" + re.escape(name) + r"\s*\(", body)
            if not dtor:
                continue
            control = re.compile(
                r"(?:"
                + re.escape(name)
                + r"\s*\(\s*(?:const\s+"
                + re.escape(name)
                + r"\s*&|"
                + re.escape(name)
                + r"\s*&&)"
                r"|operator\s*=\s*\(\s*(?:const\s+"
                + re.escape(name)
                + r"\s*&|"
                + re.escape(name)
                + r"\s*&&)"
                r"|" + re.escape(name) + r"\s*\([^)]*\)\s*=\s*(?:default|delete)"
                r"|operator\s*=\s*\([^)]*\)\s*=\s*(?:default|delete))"
            )
            if control.search(body):
                continue
            members = {m.group(1) for m in _MEMBER_POINTER.finditer(body)}
            owned = sorted(
                member
                for member in members
                if re.search(r"\bdelete\s*(?:\[\s*\])?\s*" + re.escape(member) + r"\b", body)
            )
            if not owned:
                continue
            offset = body_start + dtor.start()
            self._finding(
                "rule-of-five-missing",
                offset,
                offset + len(dtor.group(0)) - 1,
                "violation",
                "class %s owns a raw pointer and declares a destructor but no copy or move control"
                % name,
                name,
                evidence={"owning_members": owned},
            )

    def raw_pointer_nonowning(self):
        assigned = set(re.findall(r"\b([A-Za-z_]\w*)\s*=(?!=)", self.code))
        for span_start, body in self._parameters():
            for match in _RAW_POINTER_PARAM.finditer("(" + body):
                name = match.group(1)
                if name in assigned or name in _KEYWORDS:
                    continue
                if re.search(r"\bdelete\s*(?:\[\s*\])?\s*" + re.escape(name) + r"\b", self.code):
                    continue
                offset = span_start + match.start(1) - 1
                self._finding(
                    "raw-pointer-nonowning",
                    offset,
                    offset + len(name) - 1,
                    "benign_in_context",
                    "non-owning raw pointer parameter: legal C++, never a violation",
                    name,
                    evidence={"parameter": body.strip()[:200]},
                )

    def redundant_container_copy(self):
        for match in re.finditer(
            r"\bauto\s+(?:const\s+)?&{0,2}\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;", self.code
        ):
            target, source = match.group(1), match.group(2)
            entry = self.container_vars.get(source)
            if not entry or target == source:
                continue
            self._finding(
                "redundant-container-copy",
                match.start(),
                match.end() - 1,
                "violation",
                "%s is copied into %s; a container copy should be a move" % (source, target),
                target,
                evidence={"source_type": entry["type"]},
            )

    def pass_by_value_container(self):
        for span_start, body in self._parameters():
            # Matched against the real text with one character of look-ahead, not against the
            # parameter slice: the declaration lookahead needs the delimiter that follows the
            # parameter, which a slice has already cut off.
            for match in _CONTAINER_DECL.finditer(
                self.code, span_start, span_start + len(body) + 1
            ):
                if "&" in match.group(0):
                    continue
                offset = span_start + match.start()
                self._finding(
                    "pass-by-value-container",
                    offset,
                    offset + len(match.group(1)) - 1,
                    "violation",
                    "%s is passed by value, so every call copies the container" % match.group(1),
                    match.group(2),
                    evidence={"parameter": match.group(0).strip()[:120]},
                )

    def index_loop_container(self):
        for pattern, message in (
            (_INDEX_LOOP, "index loop over a container where a range loop belongs"),
            (_ITERATOR_LOOP, "hand-written iterator loop where a range loop belongs"),
        ):
            for match in pattern.finditer(self.code):
                self._finding(
                    "index-loop-container",
                    match.start(),
                    match.end() - 1,
                    "violation",
                    message,
                    evidence={"loop": match.group(0).strip()[:200]},
                )

    def string_concat_loop(self):
        for body_start, body_end in self._loop_bodies():
            for match in _STRING_ADD.finditer(self.code[body_start:body_end]):
                name = match.group(1)
                if name not in self.string_vars:
                    continue
                offset = body_start + match.start(1)
                self._finding(
                    "string-concat-loop",
                    offset,
                    offset + len(name) - 1,
                    "violation",
                    "%s is grown with += inside a loop; reserve once and append" % name,
                    name,
                    evidence={"string_type": self.container_vars[name]["type"]},
                )

    def move_on_const(self):
        for match in _MOVE.finditer(self.code):
            name = match.group(1)
            if name not in self.const_vars:
                continue
            declaration = re.search(r"\bconst[^;{}()]*\b" + re.escape(name) + r"\b", self.code)
            self._finding(
                "move-on-const",
                match.start(),
                match.end() - 1,
                "violation",
                "std::move applied to %s, which is const" % name,
                name,
                evidence={"declaration": declaration.group(0).strip()[:120] if declaration else ""},
            )

    def throw_in_destructor(self):
        for name, start, _body_start, body_end in self.classes:
            body = self.code[start:body_end]
            for dtor in re.finditer(r"~\s*" + re.escape(name) + r"\s*\(([^)]*)\)([^;{]*)", body):
                offset = start + dtor.start()
                if re.search(r"noexcept\s*\(\s*false\s*\)", dtor.group(2)):
                    self._finding(
                        "throw-in-destructor",
                        offset,
                        offset + len(dtor.group(0)) - 1,
                        "hint",
                        "~%s is noexcept(false): callers lose the destructor guarantee" % name,
                        name,
                        confidence="medium",
                    )
                # `body` is a slice of the file, so every index below is body-relative and only
                # the reported offsets are shifted back to file coordinates.
                cursor = dtor.end()
                while cursor < len(body) and body[cursor] in " \t\r\n":
                    cursor += 1
                if cursor >= len(body) or body[cursor] != "{":
                    continue
                block_end = _match_delimited(body, cursor, "{", "}")
                if block_end == -1:
                    continue
                block = body[cursor + 1 : block_end]
                for thrown in re.finditer(r"\bthrow\b", block):
                    spot = cursor + 1 + thrown.start()
                    self._finding(
                        "throw-in-destructor",
                        start + spot,
                        start + spot + len(thrown.group(0)) - 1,
                        "violation",
                        "~%s can throw: unwinding out of a destructor terminates" % name,
                        name,
                        evidence={"statement": block[thrown.start() :].split("\n")[0][:120]},
                    )

    def no_automatic_move(self):
        # `return local;` is free when copy elision applies -- one return statement and nobody
        # took the variable's address -- so it is only a defect when the local is genuinely copied:
        # returned from more than one place, or whose address escapes and blocks elision.
        returns = {}
        for match in re.finditer(r"\breturn\s+([A-Za-z_]\w*)\s*;", self.code):
            returns[match.group(1)] = returns.get(match.group(1), 0) + 1
        escaped = set(re.findall(r"&\s*(?:const\s+)?([A-Za-z_]\w*)\b", self.code))
        for match in re.finditer(r"\breturn\s+([A-Za-z_]\w*)\s*;", self.code):
            name = match.group(1)
            entry = self.container_vars.get(name)
            if not entry or entry["parameter"]:
                continue
            if returns.get(name, 0) < 2 and name not in escaped:
                continue
            self._finding(
                "no-automatic-move",
                match.start(),
                match.end() - 1,
                "violation",
                "return %s leans on an implicit move; returning std::move(%s) states the cost"
                % (name, name),
                name,
                evidence={"type": entry["type"]},
            )

    def scan(self):
        self.raw_owning_pointer()
        self.raw_delete_mismatch()
        self.rule_of_five_missing()
        self.raw_pointer_nonowning()
        self.redundant_container_copy()
        self.pass_by_value_container()
        self.index_loop_container()
        self.string_concat_loop()
        self.move_on_const()
        self.throw_in_destructor()
        self.no_automatic_move()
        self.findings.sort(key=lambda f: (f["line"], f["rule"], f["column"]))
        return self.findings


def scan_text(path, text):
    """Findings for one file, each anchored to the offending token."""
    return _File(path, text).scan()


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
        try:
            parsed = scan_text(rel, source)
        except ValueError as error:
            # A file the scanner cannot read to the end is an unanswered check, never a clean one.
            files.append({"path": rel, "parsed": False, "error": str(error)})
            incomplete = True
            continue
        files.append({"path": rel, "parsed": True, "error": None})
        findings.extend(parsed)
    findings.sort(key=lambda f: (f["path"], f["line"], f["rule"], f["column"]))
    return {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "opportunities": sorted(options["opportunities"]),
        "tags": sorted(options["tags"]),
        "complete": not incomplete,
        "files": files,
        "findings": findings,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_cpp_scan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--opportunities", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv[1:])
    options = {
        "opportunities": {item for item in args.opportunities.split(",") if item},
        "tags": {item for item in args.tags.split(",") if item},
    }
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
