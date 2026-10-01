"""Static symbol index for Rust sources (no compilation, no execution).

This is a structural scan, not a Rust parser: it sanitises comments, strings and character
literals, then walks items by brace depth. It finds items declared in modules, ``impl`` blocks and
traits, and deliberately ignores everything inside function bodies (local items are private
implementation detail) and struct/enum bodies (fields and variants are not symbols). A file whose
braces do not balance is reported in ``unparsed_paths`` instead of being indexed from a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex

_RAW_STRING = re.compile(r'r(?P<hashes>#*)".*?"(?P=hashes)', re.DOTALL)
_STRING = re.compile(r'b?"(?:\\.|[^"\\])*"', re.DOTALL)
_CHAR = re.compile(r"b?'(?:\\.[^']*|[^'\\])'")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_TOKEN = re.compile(
    r"(?P<impl>\bimpl\b(?P<ihead>[^{;]*))"
    r"|(?P<item>(?P<vis>\bpub(?:\s*\([^)]*\))?\s+)?"
    r"(?:(?:async|const|unsafe|extern(?:\s+\"\")?|default)\s+)*"
    r"\b(?P<kind>fn|struct|enum|trait|union|type|mod|static|const)\s+(?P<name>[A-Za-z_]\w*))"
    r"|(?P<open>\{)|(?P<close>\})|(?P<semi>;)"
)
_KIND = {
    "fn": "function",
    "struct": "class",
    "enum": "class",
    "trait": "class",
    "union": "class",
    "type": "class",
    "mod": "module",
    "static": "variable",
    "const": "variable",
}


def _blank(match: re.Match[str]) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text: str) -> str:
    """Blank out comments, strings and char literals, keeping every newline (line numbers)."""
    for pattern in (_COMMENT, _RAW_STRING, _STRING, _CHAR):
        text = pattern.sub(_blank, text)
    return text


@dataclass
class _Frame:
    kind: str  # root | mod | impl | trait | fn | other
    name: str = ""
    public: bool = True
    trait_impl: bool = False
    symbol: int | None = None  # index into the symbol list whose end_line this frame fixes


@dataclass
class _Walk:
    path: str
    prefix: str
    text: str
    symbols: list[Symbol] = field(default_factory=list)
    frames: list[_Frame] = field(default_factory=lambda: [_Frame("root")])
    pending: _Frame | None = None

    def line(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def qualified(self, name: str) -> str:
        parts = [self.prefix, *(f.name for f in self.frames if f.kind in {"mod", "impl", "trait"})]
        return "::".join(p for p in (*parts, name) if p)

    def container(self) -> _Frame:
        return self.frames[-1]

    def run(self) -> bool:
        for token in _TOKEN.finditer(self.text):
            if token.group("impl") is not None:
                self._impl(token)
            elif token.group("item") is not None:
                self._item(token)
            elif token.group("open") is not None:
                self.frames.append(self.pending or _Frame("other"))
                self.pending = None
            elif token.group("close") is not None:
                if len(self.frames) == 1:
                    return False
                frame = self.frames.pop()
                if frame.symbol is not None:
                    self._end(frame.symbol, self.line(token.start()))
            elif token.group("semi") is not None and self.pending is not None:
                if self.pending.symbol is not None:
                    self._end(self.pending.symbol, self.line(token.start()))
                self.pending = None
        return len(self.frames) == 1

    def _end(self, index: int, line: int) -> None:
        symbol = self.symbols[index]
        self.symbols[index] = symbol.model_copy(update={"end_line": max(line, symbol.start_line)})

    def _impl(self, token: re.Match[str]) -> None:
        if self.container().kind not in {"root", "mod"}:
            self.pending = _Frame("other")
            return
        head = " ".join(token.group("ihead").split())
        head = re.sub(r"^<.*?>\s*", "", head)  # impl generics
        trait_impl = " for " in f" {head} "
        target = head.split(" for ")[-1] if trait_impl else head
        name = re.split(r"[<\s{]", target.strip().lstrip("&").removeprefix("mut ").strip())[0]
        self.pending = _Frame("impl", name or "_", trait_impl=trait_impl)

    def _item(self, token: re.Match[str]) -> None:
        kind = token.group("kind")
        name = token.group("name")
        where = self.container()
        if where.kind not in {"root", "mod", "impl", "trait"}:
            # Inside a function/struct body: local item, not part of the public surface. A nested
            # `fn` still has a body whose braces must be tracked.
            self.pending = _Frame("other") if kind in {"fn", "mod", "trait"} else self.pending
            return
        declared_public = bool(token.group("vis")) and "(" not in (token.group("vis") or "")
        in_trait = where.kind == "trait"
        public = (declared_public or where.trait_impl or in_trait) and where.public
        if kind == "mod":
            frame = _Frame("mod", name, public=public)
        elif kind == "trait":
            frame = _Frame("trait", name, public=public)
        elif kind == "fn":
            frame = _Frame("fn")
        else:
            frame = _Frame("other")
        symbol_kind = _KIND[kind]
        if kind == "fn" and where.kind in {"impl", "trait"}:
            symbol_kind = "method"
        start = self.line(token.start("kind"))
        self.symbols.append(
            Symbol(
                symbol_kind=symbol_kind,  # type: ignore[arg-type]
                qualified_name=self.qualified(name),
                path=self.path,
                start_line=start,
                end_line=start,
                public=public,
                # Rust signatures are always fully typed, so every function counts as annotated.
                annotated=True if kind == "fn" else None,
            )
        )
        frame.symbol = len(self.symbols) - 1
        self.pending = frame


def _module_prefix(path: str) -> str:
    parts = path.removesuffix(".rs").split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] in {"lib", "main", "mod"}:
        parts = parts[:-1]
    if parts[:1] == ["bin"]:
        parts = parts[1:]
    return "::".join(["crate", *parts])


def index_rust_sources(source: ArtifactReader) -> SymbolIndex:
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in source.list():
        if not path.endswith(".rs"):
            continue
        try:
            text = sanitize(source.read(path).decode("utf-8"))
        except UnicodeDecodeError:
            unparsed.append(path)
            continue
        walk = _Walk(path=path, prefix=_module_prefix(path), text=text)
        if not walk.run():
            unparsed.append(path)
            continue
        symbols.append(
            Symbol(
                symbol_kind="module",
                qualified_name=walk.prefix,
                path=path,
                start_line=1,
                end_line=max(1, text.count("\n") + 1),
                public=True,
            )
        )
        symbols.extend(walk.symbols)
    return SymbolIndex(language_id="rust", symbols=tuple(symbols), unparsed_paths=tuple(unparsed))
