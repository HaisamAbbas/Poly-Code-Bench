"""A static C++ symbol index: what a reviewer can see without compiling anything.

This is a lexical index, not a front end. It blanks comments, string and character literals
first so a declaration inside a string cannot be mistaken for code, matches braces to find each
type's span, and marks a file ``unparsed`` when its braces do not balance — an unbalanced file has
an unknown symbol set, and reporting fewer symbols than exist would be worse than reporting that
the file was not understood.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex

SymbolKind = Literal["module", "class", "function", "method", "variable"]

_LITERAL = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
_TYPE = re.compile(r"\b(?:class|struct|union)\s+(?:[A-Z_]+\s+)*([A-Za-z_]\w*)")
_NAMESPACE = re.compile(r"\bnamespace\s+([A-Za-z_]\w*)")
_FUNCTION = re.compile(r"(?<![\w.>])(~?[A-Za-z_]\w*)\s*\(")
_ACCESS = re.compile(r"\b(public|private|protected)\s*:")
# Control-flow and cast keywords take a parenthesis but declare nothing.
_NOT_A_DECLARATION = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "sizeof",
        "alignof",
        "decltype",
        "noexcept",
        "static_assert",
        "throw",
        "new",
        "delete",
        "and",
        "or",
        "not",
        "defined",
    }
)
_TEMPLATE_NOISE = re.compile(r"<[^<>;{}]*>")
_SUFFIXES = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")


def sanitize(text: str) -> str:
    """Blank comments and literals, preserving every newline so line numbers stay true."""

    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _LITERAL.sub(blank, text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _end_of_scope(code: str, start: int) -> int:
    """The offset just past the ``}`` that closes the ``{`` at or after ``start``."""
    depth = 0
    for index in range(start, len(code)):
        character = code[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


@dataclass(frozen=True)
class _Span:
    kind: str
    name: str
    start: int
    end: int
    public: bool


def _spans(code: str) -> list[_Span]:
    """Every class/namespace body with its extent, innermost first."""
    found: list[_Span] = []
    for pattern, kind in ((_TYPE, "class"), (_NAMESPACE, "module")):
        for match in pattern.finditer(code):
            brace = code.find("{", match.end())
            if brace == -1 or code[code.find(";", match.end()) : brace].strip():
                continue
            end = _end_of_scope(code, brace)
            if end == -1:
                continue
            access = list(_ACCESS.finditer(code, max(0, match.start() - 400), match.start()))
            found.append(
                _Span(
                    kind=kind,
                    name=match.group(1),
                    start=match.start(),
                    end=end,
                    public=not access or access[-1].group(1) == "public",
                )
            )
    return sorted(found, key=lambda span: span.start)


def index_cpp_sources(source: ArtifactReader) -> SymbolIndex:
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in sorted(source.list()):
        if not path.endswith(_SUFFIXES):
            continue
        try:
            raw = source.read(path).decode("utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            unparsed.append(path)
            continue
        code = sanitize(raw)
        if code.count("{") != code.count("}"):
            unparsed.append(path)
            continue
        symbols.append(
            Symbol(
                kind="symbol",
                symbol_kind="module",
                qualified_name=path,
                path=path,
                start_line=1,
                end_line=max(1, code.count("\n") + 1),
                public=True,
                annotated=False,
            )
        )
        spans = _spans(code)
        for span in spans:
            symbols.append(
                Symbol(
                    kind="symbol",
                    symbol_kind="class" if span.kind == "class" else "module",
                    qualified_name=_qualified(spans, span, span.name),
                    path=path,
                    start_line=_line_of(code, span.start),
                    end_line=_line_of(code, span.end),
                    public=span.public,
                    annotated=True,
                )
            )
        symbols.extend(_functions(code, spans, path))
    return SymbolIndex(
        kind="symbol_index",
        language_id="cpp",
        symbols=tuple(symbols),
        unparsed_paths=tuple(unparsed),
    )


def _functions(code: str, spans: list[_Span], path: str) -> list[Symbol]:
    """Function declarations with the innermost enclosing scope as their qualifier."""
    found: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    for match in _FUNCTION.finditer(code):
        name = match.group(1)
        if name in _NOT_A_DECLARATION:
            continue
        before = code[max(0, match.start() - 64) : match.start()]
        if before.rstrip().endswith(("=", "&", "|", ",", "+", "-", "*", "/", "<", ">", "?", ":")):
            # A call, a cast or an initialiser, not a declaration.
            continue
        offset = match.start()
        enclosing = next((span for span in spans if span.start < offset < span.end), None)
        member = enclosing is not None and enclosing.kind == "class"
        kind: SymbolKind = "method" if member else "function"
        signature = _signature(code, offset)
        line = _line_of(code, offset)
        if (name, line) in seen:
            continue
        seen.add((name, line))
        found.append(
            Symbol(
                kind="symbol",
                symbol_kind=kind,
                qualified_name=(f"{_qualified(spans, enclosing, name)}" if enclosing else name),
                path=path,
                start_line=line,
                end_line=_line_of(code, offset + len(signature)),
                public=enclosing.public if enclosing is not None else True,
                # A C++ declaration carries a full type by definition.
                annotated=True,
            )
        )
    return found


def _signature(code: str, offset: int) -> str:
    """From a declaration's name to its closing parenthesis."""
    depth = 0
    for index in range(offset, min(len(code), offset + 2048)):
        character = code[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return code[offset : index + 1]
        elif character in ";\n" and depth == 0:
            break
    return ""


def _qualified(spans: list[_Span], span: _Span, name: str) -> str:
    outer = [other.name for other in spans if other.start < span.start and span.end < other.end]
    return "::".join([*outer, name])


__all__ = ["index_cpp_sources", "sanitize"]
