"""Static symbol index for Go sources (no compilation, no execution).

This is a structural scan, not a Go parser: it sanitises comments and literals, then walks
declarations by brace depth. It finds package-level declarations and methods, and deliberately
ignores everything inside function bodies (local declarations are implementation detail). A file
whose braces do not balance is reported in ``unparsed_paths`` instead of being indexed from a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex

# One alternation, scanned left to right, so whichever construct starts first wins: a `//` inside a
# string literal is string content, and a `"` inside a rune literal does not open a string.
_LITERAL = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|`(?:[^`\\]|\\.)*`"
    r"|\"(?:\\.|[^\"\\\n])*\""
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)
_TOKEN = re.compile(
    r"(?P<bol>^|\n)[ \t]*func[ \t]+(?:\([ \t]*(?P<recv>[^)]*?)[ \t]*\)[ \t]*)?"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\("
    r"|(?P<bol2>^|\n)[ \t]*(?P<gen>type|var|const)[ \t]+(?P<gname>[A-Za-z_]\w*)\b"
    r"|(?P<open>\{)|(?P<close>\})",
    re.MULTILINE,
)
_KIND = {"type": "class", "var": "variable", "const": "variable"}


def _blank(match: re.Match[str]) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def sanitize(text: str) -> str:
    """Blank out comments, strings and rune literals, keeping every newline (line numbers)."""
    return _LITERAL.sub(_blank, text)


@dataclass
class _Walk:
    path: str
    package: str
    text: str
    symbols: list[Symbol] = field(default_factory=list)
    depth: int = 0
    pending: int | None = None

    def line(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def run(self) -> bool:
        for token in _TOKEN.finditer(self.text):
            if token.group("open") is not None:
                self.depth += 1
                self.pending = None
            elif token.group("close") is not None:
                self.depth -= 1
                if self.depth < 0:
                    return False
                self.pending = None
            elif token.group("name") is not None:
                if self.depth != 0:
                    # Inside a function or composite literal: local declaration, not surface.
                    continue
                self._add(token, kind="method" if token.group("recv") else "function")
            elif token.group("gen") is not None:
                if self.depth != 0:
                    continue
                self._add(token, kind=_KIND[token.group("gen")])
        return self.depth == 0

    def _add(self, token: re.Match[str], *, kind: str) -> None:
        name = token.group("name") or token.group("gname")
        start = self.line(token.start())
        prefix = f"{self.package}." if self.package else ""
        receiver = (token.group("recv") or "").strip()
        if receiver:
            receiver_name = receiver.split()[-1].lstrip("*").strip()
            if receiver_name:
                prefix = f"{prefix}{receiver_name}."
        self.symbols.append(
            Symbol(
                symbol_kind=kind,  # type: ignore[arg-type]
                qualified_name=f"{prefix}{name}",
                path=self.path,
                start_line=start,
                end_line=start,
                public=name[:1].isupper(),
                # Go is statically typed with no annotations, so "annotated" has no meaning.
                annotated=None,
            )
        )


def _package_prefix(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".go").removesuffix("_test")


def index_go_sources(source: ArtifactReader) -> SymbolIndex:
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in source.list():
        if not path.endswith(".go") or path.endswith("_test.go"):
            continue
        try:
            text = sanitize(source.read(path).decode("utf-8"))
        except UnicodeDecodeError:
            unparsed.append(path)
            continue
        walk = _Walk(path=path, package=_package_prefix(path), text=text)
        if not walk.run():
            unparsed.append(path)
            continue
        symbols.extend(walk.symbols)
    return SymbolIndex(language_id="go", symbols=tuple(symbols), unparsed_paths=tuple(unparsed))
