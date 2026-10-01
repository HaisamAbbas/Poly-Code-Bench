"""Static symbol index for Python sources (no import, no execution)."""

from __future__ import annotations

import ast

from polycodebench_plugins_api import ArtifactReader, Symbol, SymbolIndex


def _public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _annotated(node: ast.FunctionDef | ast.AsyncFunctionDef, *, method: bool) -> bool:
    args = node.args
    every = args.posonlyargs + args.args + args.kwonlyargs
    if method and every and every[0].arg in {"self", "cls"}:
        every = every[1:]
    extras = [a for a in (args.vararg, args.kwarg) if a is not None]
    return all(a.annotation is not None for a in [*every, *extras]) and (
        node.returns is not None or node.name == "__init__"
    )


def index_python_sources(source: ArtifactReader) -> SymbolIndex:
    symbols: list[Symbol] = []
    unparsed: list[str] = []
    for path in source.list():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source.read(path), filename=path)
        except (SyntaxError, ValueError, RecursionError):
            unparsed.append(path)
            continue
        module = path.removesuffix(".py").replace("/", ".")
        symbols.append(
            Symbol(
                symbol_kind="module",
                qualified_name=module,
                path=path,
                start_line=1,
                end_line=max(1, getattr(tree.body[-1], "end_lineno", 1) if tree.body else 1),
                public=True,
            )
        )
        _walk(tree.body, module, path, symbols, in_class=False, public_scope=True)
    return SymbolIndex(language_id="python", symbols=tuple(symbols), unparsed_paths=tuple(unparsed))


def _walk(
    body: list[ast.stmt],
    prefix: str,
    path: str,
    out: list[Symbol],
    *,
    in_class: bool,
    public_scope: bool,
) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            public = public_scope and _public(node.name)
            out.append(
                Symbol(
                    symbol_kind="class",
                    qualified_name=f"{prefix}.{node.name}",
                    path=path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    public=public,
                )
            )
            _walk(node.body, f"{prefix}.{node.name}", path, out, in_class=True, public_scope=public)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            public = public_scope and _public(node.name)
            out.append(
                Symbol(
                    symbol_kind="method" if in_class else "function",
                    qualified_name=f"{prefix}.{node.name}",
                    path=path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    public=public,
                    annotated=_annotated(node, method=in_class),
                )
            )
        elif isinstance(node, ast.Assign | ast.AnnAssign) and not in_class:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    out.append(
                        Symbol(
                            symbol_kind="variable",
                            qualified_name=f"{prefix}.{target.id}",
                            path=path,
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            public=public_scope and _public(target.id),
                        )
                    )
