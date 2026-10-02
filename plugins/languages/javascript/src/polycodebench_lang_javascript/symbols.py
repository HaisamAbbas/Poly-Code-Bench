"""Static symbol index for JavaScript and TypeScript sources (no compilation, no execution).

This is a structural scan, not a JavaScript parser: it sanitises comments, strings and template
literals, then walks declarations by brace depth. It finds top-level and class-level classes,
functions, exported bindings and arrow-function consts, and deliberately ignores declarations
inside function bodies (a nested declaration is private implementation detail). A file whose braces
do not balance is reported in ``unparsed_paths`` instead of being indexed from a guess.

``annotated`` is set only for a symbol that carries an explicit return-type annotation, which is
TypeScript-only syntax. JavaScript sources therefore index with no annotated symbols at all, and a
TypeScript symbol written without a return type is recorded as unknown rather than as unannotated.

Object-literal members are not indexed: an object literal has no declaration syntax of its own, so
its members cannot be told apart from ordinary calls by a structural scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex

# One alternation, scanned left to right, so whichever construct starts first wins: a `//` inside a
# string literal is string content, and a `(` inside a comment does not open a parameter list.
_LITERAL = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|`(?:\\.|[^`\\])*`"
    r"|'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"',
    re.DOTALL,
)
# Keywords that would otherwise read as a method declaration: ``if (x) {`` is not a method.
_NOT_A_METHOD = "if|for|while|switch|catch|return|do|else|try|function|with|typeof|await|yield"
_TOKEN = re.compile(
    r"(?P<export>\bexport\b)"
    r"|(?P<class_decl>\bclass\s+(?P<class_name>[A-Za-z_$][\w$]*))"
    r"|(?P<func_decl>\bfunction\s*\*?\s*(?P<func_name>[A-Za-z_$][\w$]*)"
    r"\s*\((?:[^()]|\([^()]*\))*\)\s*(?P<func_ret>:[^;{]*)?)"
    r"|(?P<arrow>\b(?P<arrow_name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?P<arrow_param>\([^()]*\)|[A-Za-z_$][\w$]*)\s*(?P<arrow_ret>:[^=;]*?)?=>)"
    r"|(?P<method>(?!(?:" + _NOT_A_METHOD + r")\b)"
    r"(?:async\s+|get\s+|set\s+|\*\s+)*(?P<method_name>[A-Za-z_$][\w$]*)\s*"
    r"\((?:[^()]|\([^()]*\))*\)\s*(?P<method_ret>:[^;{}]*)?(?=\{))"
    r"|(?P<open>\{)|(?P<close>\})"
)
CANDIDATE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "javascript": (".js", ".mjs", ".cjs"),
    "typescript": (".ts", ".mts", ".cts", ".tsx"),
}
_DECLARING_FRAMES = frozenset({"root", "class"})


def _blank(match: re.Match[str]) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text: str) -> str:
    """Blank out comments, strings and template literals, keeping every newline (line numbers)."""
    return _LITERAL.sub(_blank, text)



def _annotated(token: re.Match[str], group: str) -> bool | None:
    """Whether the matched declaration head carries an explicit return-type annotation.

    TypeScript writes the return type between the parameter list and the body; JavaScript never
    writes one, so a JavaScript symbol is recorded as unknown rather than as unannotated.
    """
    return True if token.group(group) is not None else None


@dataclass
class _Frame:
    kind: str  # root | class | fn | other
    name: str = ""
    symbol: int | None = None  # index into the symbol list whose end_line this frame fixes


@dataclass
class _Walk:
    path: str
    prefix: str
    text: str
    symbols: list[Symbol] = field(default_factory=list)
    frames: list[_Frame] = field(default_factory=lambda: [_Frame("root")])
    pending: _Frame | None = None
    exported: bool = False

    def line(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def qualified(self, name: str) -> str:
        classes = [frame.name for frame in self.frames if frame.kind == "class"]
        return ".".join(part for part in (self.prefix, *classes, name) if part)

    def add(
        self, symbol_kind: str, name: str, start: int, annotated: bool | None
    ) -> int:
        self.symbols.append(
            Symbol(
                symbol_kind=symbol_kind,  # type: ignore[arg-type]
                qualified_name=self.qualified(name),
                path=self.path,
                start_line=start,
                end_line=start,
                # In an ES module the exported binding is the module's public surface; a top-level
                # binding that is not exported stays inside the module.
                public=self.exported,
                annotated=annotated,
            )
        )
        self.exported = False
        return len(self.symbols) - 1

    def run(self) -> bool:
        for token in _TOKEN.finditer(self.text):
            if token.group("export") is not None:
                self.exported = True
            elif token.group("class_decl") is not None:
                self._class(token)
            elif token.group("func_decl") is not None:
                self._function(token)
            elif token.group("arrow") is not None:
                self._arrow(token)
            elif token.group("method") is not None:
                self._method(token)
            elif token.group("open") is not None:
                self.frames.append(self.pending or _Frame("other"))
                self.pending = None
                if self.exported:
                    # `export { a, b }` names existing bindings; it declares nothing.
                    self.exported = False
            elif token.group("close") is not None:
                if len(self.frames) == 1:
                    return False
                frame = self.frames.pop()
                if frame.symbol is not None:
                    self._end(frame.symbol, self.line(token.start()))
        return len(self.frames) == 1

    def _end(self, index: int, line: int) -> None:
        symbol = self.symbols[index]
        self.symbols[index] = symbol.model_copy(update={"end_line": max(line, symbol.start_line)})

    def _class(self, token: re.Match[str]) -> None:
        name = token.group("class_name")
        start = self.line(token.start("class_name"))
        if self.frames[-1].kind not in _DECLARING_FRAMES:
            # Inside a function body: private detail, but its braces still have to be tracked.
            self.pending = _Frame("other")
            return
        self.pending = _Frame("class", name, self.add("class", name, start, None))

    def _function(self, token: re.Match[str]) -> None:
        name = token.group("func_name")
        start = self.line(token.start("func_name"))
        if self.frames[-1].kind not in _DECLARING_FRAMES:
            self.pending = _Frame("other")
            return
        index = self.add("function", name, start, _annotated(token, "func_ret"))
        self.pending = _Frame("fn", name, index)

    def _arrow(self, token: re.Match[str]) -> None:
        start = self.line(token.start("arrow_name"))
        if self.frames[-1].kind not in _DECLARING_FRAMES:
            self.pending = _Frame("other")
            return
        name = token.group("arrow_name")
        index = self.add("function", name, start, _annotated(token, "arrow_ret"))
        # An arrow with an expression body has no braces of its own, so it frames nothing; taking
        # the next `{` would swallow the enclosing statement's block.
        if self.text[token.end() :].lstrip().startswith("{"):
            self.pending = _Frame("fn", name, index)

    def _method(self, token: re.Match[str]) -> None:
        if self.frames[-1].kind != "class":
            # Outside a class body this is a call or an object literal, not a declaration.
            return
        name = token.group("method_name")
        start = self.line(token.start("method_name"))
        index = self.add("method", name, start, _annotated(token, "method_ret"))
        self.pending = _Frame("fn", name, index)


def _module_prefix(path: str) -> str:
    """Dotted module name for a source path (``src/lib/count.js`` -> ``lib.count``)."""
    parts = path.replace("\\", "/").rsplit(".", 1)[0].split("/")
    if parts[:1] == ["src"] and len(parts) > 1:
        parts = parts[1:]
    if len(parts) > 1 and parts[-1] in {"index", "main"}:
        parts = parts[:-1]
    return ".".join(part for part in parts if part) or _file_stem(path)


def _file_stem(path: str) -> str:
    """The bare file name without its extension, for a path that has no directory part."""
    stem = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem or path


def index_js_sources(source: ArtifactReader, language_id: str = "javascript") -> SymbolIndex:
    """Index every candidate source file of one language.

    ``language_id`` selects which suffixes are candidates and what the resulting index declares, so
    a JavaScript plugin never indexes (or claims) TypeScript sources.
    """
    suffixes = CANDIDATE_SUFFIXES[language_id]
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in source.list():
        if not path.endswith(suffixes):
            continue
        try:
            text = sanitize(source.read(path).decode("utf-8"))
        except UnicodeDecodeError:
            unparsed.append(path)
            continue
        prefix = _module_prefix(path)
        walk = _Walk(path=path, prefix=prefix, text=text)
        if not walk.run():
            unparsed.append(path)
            continue
        symbols.append(
            Symbol(
                symbol_kind="module",
                qualified_name=prefix,
                path=path,
                start_line=1,
                end_line=max(1, text.count("\n") + 1),
                # A module is reached by its path, so it is public to whoever imports it.
                public=True,
            )
        )
        symbols.extend(walk.symbols)
    return SymbolIndex(
        language_id=language_id, symbols=tuple(symbols), unparsed_paths=tuple(unparsed)
    )


__all__ = ["CANDIDATE_SUFFIXES", "index_js_sources", "sanitize"]
