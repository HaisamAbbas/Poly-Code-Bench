"""Static symbol index for C translation units.

C has no declaration syntax a parser can lean on: a function definition, a prototype, a struct tag
and a variable declaration share most of their punctuation. This index is therefore deliberately
shallow - functions, prototypes, struct tags and file-scope variables - and it reports a file it
could not balance as unparsed rather than guessing. A wrong symbol index is worse than an
because the annotation/judge packets are built from it.
"""

from __future__ import annotations

import re
from typing import ClassVar

from polycodebench_plugins_api import ArtifactReader, PluginModel, Symbol
from pydantic import Field

SOURCE_SUFFIXES = (".c", ".h")
MAX_IDENTIFIER = 120

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)
_PREPROCESSOR = re.compile(r"^[ \t]*#.*(?:\\\n.*)*$", re.MULTILINE)
_FUNCTION = re.compile(
    r"(?P<signature>^[A-Za-z_][\w \t\*]*?\b(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{)]*)\)\s*\{)",
    re.MULTILINE,
)
_STORAGE = (
    r"(?:static|extern|inline|const|unsigned|signed|register|_Noreturn|_Thread_local|_Atomic)"
)
_PARAMETERS = r"(?:void|[A-Za-z_][\w \t\*]*?(?:\[[^\]]*\])?(?:\s*,\s*)?)*"
_FORWARD = re.compile(
    r"^[A-Za-z_][\w \t\*]*?\b(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{)]*)\)\s*;",
    re.MULTILINE,
)
_STRUCT = re.compile(r"^\s*(?:typedef\s+)?struct\s+(?P<tag>[A-Za-z_]\w*)\s*\{", re.MULTILINE)
#: ``typedef struct { ... } name;`` - an anonymous record with a typedef name. A tag rule cannot see
#: it, and the name a C program actually writes is the typedef, not a tag nobody spells out.
_STRUCT_TYPEDEF = re.compile(
    r"^\s*typedef\s+struct(?:\s+[A-Za-z_]\w*)?\s*\{[^{}]*\}\s*(?P<name>[A-Za-z_]\w*)\s*;",
    re.MULTILINE,
)
_GLOBAL = re.compile(
    r"^(?:" + _STORAGE + r")?\s*(?P<type>[A-Za-z_]\w*)\s*\*?\s*(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:\[[^\]]*\])?\s*(?:=[^;]*)?;\s*$",
    re.MULTILINE,
)
#: Identifiers that look like definitions but are keywords, macros or libc.
_RESERVED = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "return",
        "sizeof",
        "case",
        "else",
        "do",
        "typedef",
        "struct",
        "union",
        "enum",
        "static",
        "extern",
        "const",
        "unsigned",
        "signed",
        "void",
        "char",
        "short",
        "int",
        "long",
        "float",
        "double",
        "inline",
        "restrict",
        "register",
        "goto",
        "break",
        "continue",
        "default",
    }
)


def blank(source: str, *, strings: bool) -> str:
    """Blank out comments, preprocessor lines and optionally string literals, keeping every offset.

    Offsets are preserved on purpose: every rule below indexes into the *original* text so a
    line number is the line a reviewer will open. Replacing content with spaces rather than deleting
    it keeps every byte offset identical.

    ``strings=False`` is used where the literals *are* the data - the test harness's case ids live
    inside ``PCB_CASE("id", fn)``, and blanking them would hide the inventory the oracle is checked
    against.
    """
    text = _PREPROCESSOR.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)
    text = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    if strings:
        text = _STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


def sanitize(source: str) -> str:
    """Code with comments, strings and directives removed - what the symbol rules match against."""
    return blank(source, strings=True)


def strip_comments(source: str) -> str:
    """Code with only comments and directives removed; literals left readable."""
    return blank(source, strings=False)


def balanced(source: str) -> bool:
    """Whether braces balance. A file that does not is reported, not guessed at."""
    depth = 0
    for char in source:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_static(signature: str) -> bool:
    return bool(re.search(r"\bstatic\b", signature))


def _returns_const(signature: str) -> bool:
    """Whether the declared return type is ``const``-qualified.

    C has no type annotations, so this is the honest analogue of the flag the shared contract calls
    ``annotated``: it is the one declaration-level property the C idiom rubric actually scores
    (``const_type_portability``). Reporting ``None`` would mean "no expectation", which would then
    let a const-oblivious interface read as an unpinned task rather than a miss.
    """
    head = signature.split("(", 1)[0]
    return "const" in head


def _is_declaration(specifiers: str, type_name: str) -> bool:
    if type_name in _RESERVED:
        return False
    return not specifiers.strip()


def index_c_sources(source: ArtifactReader) -> SymbolIndex:
    """Index every readable C source and header the reader offers."""
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in sorted(source.list()):
        if not path.endswith(SOURCE_SUFFIXES):
            continue
        try:
            raw = source.read(path)
        except (FileNotFoundError, OSError):
            unparsed.append(path)
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            unparsed.append(path)
            continue
        if not balanced(sanitize(text)):
            unparsed.append(path)
            continue
        symbols.extend(_file_symbols(path, text))
    return SymbolIndex(
        language_id="c",
        symbols=tuple(symbols),
        unparsed_paths=tuple(unparsed),
    )


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _file_symbols(path: str, source: str) -> list[Symbol]:
    text = sanitize(source)
    found: list[Symbol] = []
    for match in _FUNCTION.finditer(text):
        name = match.group("name")
        if name in _RESERVED or not match.group("params").strip():
            # An empty parameter list is a K&R definition or a macro invocation, not a signature.
            continue
        start = match.start("name")
        end = _closing_brace(text, match.end() - 1)
        found.append(
            Symbol(
                symbol_kind="function",
                qualified_name=name,
                path=path,
                start_line=_line_of(text, start),
                end_line=_line_of(text, max(end, start)),
                public=not _is_static(match.group("signature")),
                annotated=_returns_const(match.group("signature")),
            )
        )
    for match in _FORWARD.finditer(text):
        name = match.group("name")
        if name in _RESERVED or not match.group("params").strip():
            continue
        start = match.start("name")
        found.append(
            Symbol(
                # A prototype is an interface entry rather than a definition, which is the closest
                # thing this vocabulary has to a declared method.
                symbol_kind="method",
                qualified_name=name,
                path=path,
                start_line=_line_of(text, start),
                end_line=_line_of(text, start),
                public=not re.search(r"\bstatic\b", match.group(0)),
                annotated=_returns_const(match.group(0)),
            )
        )
    for match in _STRUCT.finditer(text):
        found.append(
            Symbol(
                # A record is C's class: a named type with fields and no behaviour of its own.
                symbol_kind="class",
                qualified_name=match.group("tag"),
                path=path,
                start_line=_line_of(text, match.start("tag")),
                end_line=_line_of(text, match.start("tag")),
                public=True,
                annotated=None,
            )
        )
    for match in _STRUCT_TYPEDEF.finditer(text):
        found.append(
            Symbol(
                symbol_kind="class",
                qualified_name=match.group("name"),
                path=path,
                start_line=_line_of(text, match.start()),
                end_line=_line_of(text, match.start()),
                public=True,
                annotated=None,
            )
        )
    for match in _GLOBAL.finditer(text):
        name = match.group("name")
        if not _is_declaration(match.group("type"), name):
            continue
        found.append(
            Symbol(
                symbol_kind="variable",
                qualified_name=name[:MAX_IDENTIFIER],
                path=path,
                start_line=_line_of(text, match.start("name")),
                end_line=_line_of(text, match.start("name")),
                public=True,
                annotated=False,
            )
        )
    return found


def _closing_brace(text: str, open_at: int) -> int:
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return open_at


class SymbolIndex(PluginModel):
    """Language-neutral symbol index. ``unparsed_paths`` is reported, never guessed through."""

    __test__ = False
    kind: str = "symbol_index"
    language_id: str
    symbols: tuple[Symbol, ...] = ()
    unparsed_paths: tuple[str, ...] = Field(default=())

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset()
