"""Token n-gram overlap between a candidate and a reference corpus.

Two views are compared:

- ``surface``: the candidate's tokens with comments removed. It catches verbatim or lightly edited
  copies.
- ``structural``: the same tokens with identifiers and numbers replaced by ``ID`` and ``NUM``
  (language keywords are kept). It catches renamed copies, which is the common way a problem
  survives a "we changed the variable names" cleanup.

For each view two measures are reported in basis points:

- ``union``: the share of candidate n-grams found anywhere in the corpus. This follows the PaLM
  clean/contaminated split as summarised by a secondary source (8-grams, 70% threshold); the primary
  report was not readable when this module was written, so the threshold is a pilot default.
- ``max_document``: the share found in the single most-similar reference document. Boilerplate
  shared across many files inflates ``union`` but rarely reaches a high ``max_document`` value,
  so the two measures are gated separately.

Overlap is a screen, not proof. Generic idioms still produce false positives; the report keeps
the matched document identifier so a reviewer can inspect it.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from polycodebench_core.canonical import canonical_digest

from polycodebench_taskgen.contracts import TaskgenModel

View = Literal["surface", "structural"]
VIEWS: tuple[View, ...] = ("surface", "structural")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# `#` starts a comment in Python, shell and YAML, but `#[` is a Rust attribute and is kept.
_LINE_COMMENT_RE = re.compile(r"(?m)(?<![\w\"'])(?:#(?!\[)|//).*$")
_KEYWORDS = frozenset(
    {
        "and", "as", "assert", "async", "await", "break", "case", "catch", "class", "const",
        "continue", "def", "default", "del", "do", "elif", "else", "enum", "except", "export",
        "extends", "false", "finally", "fn", "for", "from", "func", "go", "if", "impl", "import",
        "in", "interface", "is", "lambda", "let", "loop", "match", "mod", "mut", "new", "none",
        "not", "null", "or", "package", "pass", "private", "pub", "public", "raise", "return",
        "self", "static", "struct", "switch", "true", "try", "type", "use", "var", "void",
        "while", "with", "yield", "bool", "bytes", "char", "double", "float", "int", "list",
        "dict", "str", "string", "usize", "u8", "u32", "u64", "i32", "i64", "f64", "vec",
    }
)  # fmt: skip
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".mypy_cache"})


class ViewOverlap(TaskgenModel):
    view: View
    candidate_ngrams: int
    union_bp: int
    max_document_bp: int
    max_document_id: str | None


class OverlapSummary(TaskgenModel):
    views: tuple[ViewOverlap, ...]

    def view(self, name: View) -> ViewOverlap:
        return next(item for item in self.views if item.view == name)


def surface_tokens(text: str) -> list[str]:
    without_blocks = _BLOCK_COMMENT_RE.sub(" ", text)
    return _TOKEN_RE.findall(_LINE_COMMENT_RE.sub(" ", without_blocks))


def structural_tokens(text: str) -> list[str]:
    normalised: list[str] = []
    for token in surface_tokens(text):
        first = token[0]
        if first.isdigit():
            normalised.append("NUM")
        elif first.isalpha() or first == "_":
            normalised.append(token if token.lower() in _KEYWORDS else "ID")
        else:
            normalised.append(token)
    return normalised


def ngram_hashes(tokens: list[str], n: int) -> frozenset[int]:
    """Stable 64-bit hashes of token n-grams. Python's ``hash`` is salted, so it is not used."""
    if not tokens:
        return frozenset()
    if len(tokens) < n:
        windows: Iterable[list[str]] = [tokens]
    else:
        windows = (tokens[start : start + n] for start in range(len(tokens) - n + 1))
    hashes: set[int] = set()
    for window in windows:
        payload = "\x1f".join(window).encode("utf-8")
        hashes.add(int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big"))
    return frozenset(hashes)


def _basis_points(numerator: int, denominator: int) -> int:
    return (10000 * numerator + denominator // 2) // denominator


class ReferenceIndex:
    """Inverted index from n-gram hash to reference document identifiers.

    The index is mutable: ``add`` is how a batch screen makes an accepted candidate visible to the
    candidates that follow it, so near-duplicates inside one batch are caught too.
    """

    def __init__(self, *, ngram: int = 8) -> None:
        if not 3 <= ngram <= 64:
            raise ValueError("ngram must be between 3 and 64")
        self._ngram = ngram
        self._postings: dict[View, dict[int, set[str]]] = {view: defaultdict(set) for view in VIEWS}
        self._documents: dict[str, dict[View, frozenset[int]]] = {}

    @property
    def ngram(self) -> int:
        return self._ngram

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def add(self, document_id: str, text: str) -> None:
        if document_id in self._documents:
            raise ValueError(f"duplicate reference document: {document_id}")
        hashes = self._hash_views(text)
        for view, grams in hashes.items():
            postings = self._postings[view]
            for gram in grams:
                postings[gram].add(document_id)
        self._documents[document_id] = hashes

    def screen(self, text: str) -> OverlapSummary:
        candidate = self._hash_views(text)
        results: list[ViewOverlap] = []
        for view in VIEWS:
            grams = candidate[view]
            total = len(grams)
            if total == 0:
                results.append(ViewOverlap(view=view, candidate_ngrams=0, union_bp=0,
                                           max_document_bp=0, max_document_id=None))  # fmt: skip
                continue
            postings = self._postings[view]
            shared = 0
            per_document: Counter[str] = Counter()
            for gram in grams:
                documents = postings.get(gram)
                if documents:
                    shared += 1
                    per_document.update(documents)
            best_id: str | None = None
            best_count = 0
            if per_document:
                # Sorting makes ties resolve to the lexicographically first document.
                best_id, best_count = max(sorted(per_document.items()), key=lambda item: item[1])
            results.append(
                ViewOverlap(
                    view=view,
                    candidate_ngrams=total,
                    union_bp=_basis_points(shared, total),
                    max_document_bp=_basis_points(best_count, total),
                    max_document_id=best_id,
                )
            )
        return OverlapSummary(views=tuple(results))

    def _hash_views(self, text: str) -> dict[View, frozenset[int]]:
        return {
            "surface": ngram_hashes(surface_tokens(text), self._ngram),
            "structural": ngram_hashes(structural_tokens(text), self._ngram),
        }


def _iter_text_files(root: Path, suffixes: frozenset[str], max_bytes: int) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath, filename)
            if path.is_symlink() or path.suffix.lower() not in suffixes:
                continue
            if path.stat().st_size <= max_bytes:
                yield path


def build_reference_index(
    roots: Mapping[str, Path],
    *,
    ngram: int = 8,
    suffixes: frozenset[str] = frozenset(
        {
            ".py",
            ".rs",
            ".js",
            ".ts",
            ".go",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".md",
            ".yaml",
            ".yml",
            ".json",
            ".toml",
            ".txt",
        }
    ),
    max_bytes: int = 512_000,
) -> tuple[ReferenceIndex, str]:
    """Index every readable text file under labelled roots.

    Returns the index and a digest over the sorted ``(document_id, sha256)`` list. The digest lets a
    screening report prove which corpus it was checked against.
    """
    index = ReferenceIndex(ngram=ngram)
    manifest: list[list[str]] = []
    for label, root in sorted(roots.items()):
        if not root.is_dir():
            raise ValueError(f"reference root is not a directory: {label}")
        for path in _iter_text_files(root, suffixes, max_bytes):
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            document_id = f"{label}/{path.relative_to(root).as_posix()}"
            index.add(document_id, text)
            manifest.append([document_id, hashlib.sha256(text.encode("utf-8")).hexdigest()])
    manifest.sort()
    return index, canonical_digest({"kind": "reference_corpus", "documents": manifest})
