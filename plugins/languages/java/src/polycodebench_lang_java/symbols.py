"""Static symbol index for Java sources (no compilation, no execution).

This is a structural scan, not a Java parser: it sanitises comments, string literals and character
literals, then walks declarations by brace depth. It finds types in the compilation unit's top-level
scope and their members, and deliberately ignores everything inside a method body (local classes
and local variables are private implementation detail). A file whose braces do not balance is
reported in ``unparsed_paths`` instead of being indexed from a guess.

Maven's convention puts ``src/main/java/demo/TopWords.java`` in package ``demo``, so a type's
qualified name is derived from the declared ``package`` and cross-checked against its directory.
A file whose ``package`` line disagrees with its path is still indexed under the declared package,
because that is what ``javac`` will compile it as. The disagreement itself is a compile error the
build plan will report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex

# One alternation, scanned left to right, so whichever construct starts first wins: a `//` inside a
# string literal is string content, and a `"` inside a char literal does not open a string. Java
# text blocks (`"""`) are matched as a unit so a brace inside one is not counted as a scope.
_LITERAL = re.compile(
    r'"""(?:.|\n)*?"""'
    r"|'(?:\\.|[^'\\\n])'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|//[^\n]*"
    r"|/\*(?:.|\n)*?\*/"
)
#: Every Java declaration modifier that may appear between the statement boundary and the
#: declaration itself. Kept as one alternation so a modifier list cannot be half-matched.
_MODIFIERS = (
    r"(?:public|protected|private|static|final|abstract|sealed|non-sealed|strictfp"
    r"|synchronized|native|transient|volatile|default)"
)
_METHOD = re.compile(
    r"(?:^|[;{}])\s*(?:" + _MODIFIERS + r"|\s)*"
    r"(?P<ret>void|boolean|int|long|double|float|char|byte|short|[\w.$<>\[\]?]+)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^;{)]*(?:\([^)]*\)[^;{)]*)*\)\s*(?:throws\s+[\w.$,\s]+)?\{"
)
_FIELD = re.compile(
    r"(?:^|[;{}])\s*(?:" + _MODIFIERS + r"|\s)*"
    r"(?P<type>[\w.$<>\[\]?]+)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:=[^;]*)?;"
)
_TYPE_BODY = re.compile(
    r"\b(?P<kind>class|interface|enum|record)\s+(?P<name>[A-Za-z_$][\w$]*)[^{;]*\{"
)
_PACKAGE = re.compile(r"^\s*package\s+([\w.$]+)\s*;", re.MULTILINE)


def _blank(match: re.Match[str]) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text: str) -> str:
    """Blank out comments, strings and char literals, keeping every newline (line numbers)."""
    return _LITERAL.sub(_blank, text)


@dataclass
class _TypeBody:
    """One type body located by its opening brace offset."""

    name: str
    qualified: str
    public: bool
    start: int
    end: int
    start_line: int


def _package(text: str) -> str:
    match = _PACKAGE.search(text)
    return match.group(1) if match else ""


def _package_from_path(path: str) -> str:
    cleaned = path.replace("\\", "/")
    for prefix in ("src/main/java/", "src/test/java/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    parts = cleaned.removesuffix(".java").split("/")
    return ".".join(parts[:-1])


def _qualified(package: str, path: str, name: str) -> str:
    return f"{package}.{name}" if package else name


def _public_prefix(text: str, offset: int) -> bool:
    """Whether the declaration starting before ``offset`` carries ``public``.

    Only the text between the previous statement boundary and the declaration keyword is examined,
    so a ``public`` on an unrelated earlier member cannot leak onto this one.
    """
    window = text.rfind(";", 0, offset)
    window = max(window, text.rfind("}", 0, offset), text.rfind("{", 0, offset))
    return bool(re.search(r"\bpublic\b", text[window + 1 : offset]))


def _type_bodies(text: str, path: str) -> tuple[list[_TypeBody], bool]:
    """Every type body in the file, plus whether the file's braces balance.

    One pass. Each type declaration's opening brace is matched to its closing brace, and a
    declaration whose brace is never closed marks the whole file ``unparsed`` rather than being
    indexed from a truncated guess.
    """
    package = _package(text) or _package_from_path(path)
    bodies: list[_TypeBody] = []
    balanced = True
    for match in _TYPE_BODY.finditer(text):
        start = match.end() - 1
        end = _matching_brace(text, start)
        if end is None:
            balanced = False
            continue
        name = match.group("name")
        bodies.append(
            _TypeBody(
                name=name,
                qualified=_qualified(package, path, name),
                public=_public_prefix(text, match.start()),
                start=start,
                end=end,
                start_line=text.count("\n", 0, start) + 1,
            )
        )
    # A stray closing brace with no opening one is also a broken file; the matcher above would
    # silently ignore it, so the overall depth is checked once at the end.
    if text.count("{") != text.count("}"):
        balanced = False
    return bodies, balanced


def _matching_brace(text: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def index_java_sources(source: ArtifactReader) -> SymbolIndex:
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in source.list():
        if not path.endswith(".java"):
            continue
        text = sanitize(source.read(path).decode("utf-8", errors="replace"))
        bodies, balanced = _type_bodies(text, path)
        if not balanced:
            unparsed.append(path)
            continue
        for body in bodies:
            symbols.append(
                Symbol(
                    symbol_kind="class",
                    qualified_name=body.qualified,
                    path=path,
                    start_line=body.start_line,
                    end_line=text.count("\n", 0, body.end) + 1,
                    public=body.public,
                )
            )
            symbols.extend(_members(text, body, path))
    return SymbolIndex(
        language_id="java", symbols=tuple(symbols), unparsed_paths=tuple(sorted(unparsed))
    )


def _members(text: str, body: _TypeBody, path: str) -> list[Symbol]:
    """Methods and fields declared directly in one type body."""
    found: list[Symbol] = []
    inner = text[body.start + 1 : body.end]
    base = body.start_line
    for match in _METHOD.finditer(inner):
        name = match.group("name")
        line = base + inner.count("\n", 0, match.start())
        found.append(
            Symbol(
                symbol_kind="method",
                qualified_name=f"{body.qualified}#{name}",
                path=path,
                start_line=line,
                end_line=line,
                public=_public_prefix(inner, match.start()),
            )
        )
    for match in _FIELD.finditer(inner):
        name = match.group("name")
        line = base + inner.count("\n", 0, match.start())
        found.append(
            Symbol(
                symbol_kind="variable",
                qualified_name=f"{body.qualified}#{name}",
                path=path,
                start_line=line,
                end_line=line,
                public=_public_prefix(inner, match.start()),
            )
        )
    return found


def qualified_names(index: SymbolIndex) -> tuple[str, ...]:
    """Every qualified name in the index, sorted; a convenience for tests and tooling."""
    return tuple(sorted({symbol.qualified_name for symbol in index.symbols}))
