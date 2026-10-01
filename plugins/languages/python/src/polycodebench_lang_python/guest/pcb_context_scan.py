"""Context-aware Python anti-pattern scanner (pure ``ast``; shipped inside the evaluator image).

The scanner exists because several idiom/quality expectations are only meaningful in context:
a mutable default argument is a defect only when the function mutates it or lets it escape; a
missing annotation matters only when the task contract expects types. Every record carries a
``verdict``: ``violation`` (counts), ``benign_in_context`` (kept as evidence, never penalised) or
``hint`` (needs reviewer/judge evidence). Counting constructs *used* never earns credit: the
scanner only reports defects; opportunity counts come from the frozen task.

Usage: python pcb_context_scan.py --root DIR --output FILE [--typing-expectation required|none]
                                  [--opportunities a,b] PATH...
Exit status: 0 completed and clean, 1 completed with violations, 2 incomplete (unreadable file).
"""

import argparse
import ast
import json
import os
import sys

SCANNER_VERSION = "1"
SCHEMA = "pcb-context-scan-v1"
MUTATORS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "sort",
        "reverse",
        "update",
        "setdefault",
        "popitem",
        "add",
        "discard",
        "appendleft",
        "extendleft",
    }
)
MUTABLE_CALLS = frozenset(
    {"list", "dict", "set", "bytearray", "deque", "defaultdict", "Counter", "OrderedDict"}
)
AGGREGATORS = {"any": "high", "all": "high", "sum": "medium", "min": "medium", "max": "medium"}
BROAD = frozenset({"Exception", "BaseException"})


class Scanner(ast.NodeVisitor):
    def __init__(self, path, tree, options):
        self.path = path
        self.options = options
        self.findings = []
        self._scope = []
        self._loop_depth = 0
        self._tree = tree
        self._function_node = tree

    # ------------------------------------------------------------------ helpers
    def qualname(self, name=None):
        parts = list(self._scope) + ([name] if name else [])
        return ".".join(parts) if parts else "<module>"

    def add(self, rule, node, verdict, confidence, message, symbol=None, **evidence):
        self.findings.append(
            {
                "rule": rule,
                "path": self.path,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno) or node.lineno,
                "column": getattr(node, "col_offset", 0) + 1,
                "symbol": symbol or self.qualname(),
                "verdict": verdict,
                "confidence": confidence,
                "message": message,
                "evidence": evidence,
            }
        )

    # ------------------------------------------------------------ scope tracking
    def visit_ClassDef(self, node):
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node):
        self._function(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _function(self, node):
        symbol = self.qualname(node.name)
        self._mutable_defaults(node, symbol)
        self._annotations(node, symbol)
        self._asserts(node, symbol)
        self._resources(node, symbol)
        self._scope.append(node.name)
        saved = self._loop_depth
        saved_function = self._function_node
        self._loop_depth = 0
        self._function_node = node
        self.generic_visit(node)
        self._loop_depth = saved
        self._function_node = saved_function
        self._scope.pop()

    # ------------------------------------------------------------ mutable defaults
    def _mutable_defaults(self, node, symbol):
        args = node.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults))
        pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in pairs:
            if not _is_mutable_default(default):
                continue
            mutated, escaped, rebound_before = _usage(node, arg.arg)
            shared = (mutated or escaped) and not rebound_before
            self.add(
                "mutable-default-shared" if shared else "mutable-default-benign",
                default,
                "violation" if shared else "benign_in_context",
                "high" if shared else "medium",
                (
                    "mutable default %r is mutated or escapes, so state is shared across calls"
                    if shared
                    else "mutable default %r is only read or defensively rebound"
                )
                % arg.arg,
                symbol=symbol,
                parameter=arg.arg,
                mutated=mutated,
                escaped=escaped,
                rebound_before_use=rebound_before,
            )

    # -------------------------------------------------------------- annotations
    def _annotations(self, node, symbol):
        if self.options["typing"] != "required":
            return
        in_class = bool(self._scope) and self._scope[-1][:1].isupper()
        if node.name.startswith("_") and not (
            node.name.startswith("__") and node.name.endswith("__")
        ):
            return
        if any(part.startswith("_") for part in self._scope):
            return
        if self._scope and not in_class:
            return  # nested function: not public API
        args = node.args
        every = args.posonlyargs + args.args + args.kwonlyargs
        if in_class and every and every[0].arg in {"self", "cls"}:
            every = every[1:]
        missing = [a.arg for a in every if a.annotation is None]
        for extra in (args.vararg, args.kwarg):
            if extra is not None and extra.annotation is None:
                missing.append(extra.arg)
        no_return = node.returns is None and node.name != "__init__"
        if missing or no_return:
            self.add(
                "missing-annotation",
                node,
                "violation",
                "high",
                "public function lacks annotations the task contract expects",
                symbol=symbol,
                missing_parameters=missing,
                missing_return=no_return,
            )

    # ------------------------------------------------------------------- asserts
    def _asserts(self, node, symbol):
        if node.name.startswith("_"):
            return
        names = {a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs}
        for stmt in node.body[:5]:
            if isinstance(stmt, ast.Assert) and any(
                isinstance(n, ast.Name) and n.id in names for n in ast.walk(stmt.test)
            ):
                self.add(
                    "assert-validation",
                    stmt,
                    "violation",
                    "medium",
                    "assert used to validate public arguments disappears under python -O",
                    symbol=symbol,
                )

    # ----------------------------------------------------------------- resources
    def _resources(self, node, symbol):
        with_items = set()
        closed = set()
        for inner in ast.walk(node):
            if isinstance(inner, (ast.With, ast.AsyncWith)):
                for item in inner.items:
                    with_items.add(id(item.context_expr))
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "close"
                and isinstance(inner.func.value, ast.Name)
            ):
                closed.add(inner.func.value.id)
        assigned = {}
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign) and isinstance(inner.value, ast.Call):
                if _is_open(inner.value) and len(inner.targets) == 1:
                    if isinstance(inner.targets[0], ast.Name):
                        assigned[id(inner.value)] = inner.targets[0].id
        returned = {
            id(inner.value)
            for inner in ast.walk(node)
            if isinstance(inner, ast.Return) and inner.value is not None
        }
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _is_open(inner):
                if id(inner) in with_items or id(inner) in returned:
                    continue
                name = assigned.get(id(inner))
                if name is not None and name in closed:
                    self.add(
                        "open-without-with-closed",
                        inner,
                        "benign_in_context",
                        "medium",
                        "file is closed explicitly; a context manager would be safer",
                        symbol=symbol,
                    )
                    continue
                self.add(
                    "open-without-context-manager",
                    inner,
                    "violation",
                    "high",
                    "file opened without a context manager and never closed in this function",
                    symbol=symbol,
                )
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "acquire"
                and not _inside_try_finally(node, inner)
            ):
                self.add(
                    "lock-acquire-without-release-guard",
                    inner,
                    "violation",
                    "medium",
                    "acquire() without with/try-finally can leave the lock held on error",
                    symbol=symbol,
                )

    # --------------------------------------------------------------------- loops
    def visit_For(self, node):
        self._range_len(node)
        self._counting_loop(node)
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def _counting_loop(self, node):
        """``count = 0`` then ``for _ in items: count += 1`` is ``len(items)``.

        Only the degenerate shape counts: the loop discards its target and its whole body is the
        increment, so the counter is exactly the length of what was iterated. A loop that reads
        the item, or that does anything else, is left alone.
        """
        if not isinstance(node.target, ast.Name) or node.target.id != "_":
            return
        body = [s for s in node.body if not (isinstance(s, ast.Expr) and _is_docstring(s))]
        if len(body) != 1 or not isinstance(body[0], ast.AugAssign):
            return
        step = body[0]
        if (
            not isinstance(step.op, ast.Add)
            or not isinstance(step.target, ast.Name)
            or not isinstance(step.value, ast.Constant)
            or step.value.value != 1
        ):
            return
        counter = step.target.id
        initialised = any(
            isinstance(inner, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == counter for t in inner.targets)
            and isinstance(inner.value, ast.Constant)
            and inner.value.value == 0
            for inner in ast.walk(self._function_node)
            if getattr(inner, "lineno", node.lineno) < node.lineno
        )
        if initialised:
            self.add(
                "manual-counter",
                node,
                "violation",
                "medium",
                "counting an iterable by hand; len() expresses this",
                evidence={"counter": counter},
            )

    def _range_len(self, node):
        it = node.iter
        if not (
            isinstance(it, ast.Call)
            and isinstance(it.func, ast.Name)
            and it.func.id == "range"
            and len(it.args) == 1
            and isinstance(it.args[0], ast.Call)
            and isinstance(it.args[0].func, ast.Name)
            and it.args[0].func.id == "len"
            and len(it.args[0].args) == 1
            and isinstance(it.args[0].args[0], ast.Name)
            and isinstance(node.target, ast.Name)
        ):
            return
        seq, index = it.args[0].args[0].id, node.target.id
        uses = [
            n
            for stmt in node.body
            for n in ast.walk(stmt)
            if isinstance(n, ast.Name) and n.id == index
        ]
        pure = all(_is_plain_index(node, use, seq, index) for use in uses)
        self.add(
            "range-len-index-loop" if pure else "range-len-index-needed",
            node,
            "violation" if pure else "benign_in_context",
            "medium" if pure else "medium",
            "loop only uses the index to read the sequence; iterate the sequence directly"
            if pure
            else "index is needed for more than reading the sequence",
        )

    # --------------------------------------------------------------------- calls
    def visit_Call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None
        if name in AGGREGATORS and len(node.args) == 1 and isinstance(node.args[0], ast.ListComp):
            self.add(
                "aggregate-over-list-comprehension",
                node,
                "violation",
                AGGREGATORS[name],
                "%s() over a list comprehension builds the whole list; use a generator" % name,
            )
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "join"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.ListComp)
        ):
            self.add(
                "aggregate-over-list-comprehension",
                node,
                "violation",
                "low",
                "join over a list comprehension; join accepts any iterable",
            )
        if isinstance(func, ast.Attribute) and func.attr == "readlines" and not node.args:
            streaming = "streaming" in self.options["opportunities"]
            self.add(
                "readlines-loads-everything",
                node,
                "violation" if streaming else "hint",
                "high" if streaming else "low",
                "readlines() loads the whole input; iterate the file for streaming tasks",
            )
        self.generic_visit(node)

    # ----------------------------------------------------------------- statements
    def visit_Raise(self, node):
        exc = node.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(target, ast.Name) and target.id in BROAD:
            self.add(
                "generic-exception-raised",
                node,
                "violation",
                "medium",
                "raise a specific exception type instead of %s" % target.id,
            )
        self.generic_visit(node)

    def visit_Try(self, node):
        for handler in node.handlers:
            self._handler(handler)
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def _handler(self, handler):
        kinds = _handler_names(handler)
        broad = handler.type is None or bool(kinds & BROAD)
        body = [s for s in handler.body if not isinstance(s, ast.Expr) or not _is_docstring(s)]
        silent = all(
            isinstance(s, (ast.Pass, ast.Continue))
            or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
            or (
                isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant))
            )
            for s in body
        )
        reraises = any(isinstance(n, ast.Raise) for s in body for n in ast.walk(s))
        uses_error = handler.name is not None and any(
            isinstance(n, ast.Name) and n.id == handler.name for s in body for n in ast.walk(s)
        )
        if handler.type is None:
            self.add("bare-except", handler, "violation", "high", "bare except hides every error")
        if silent and not reraises and not uses_error:
            self.add(
                "swallowed-broad-exception" if broad else "swallowed-narrow-exception",
                handler,
                "violation" if broad else "benign_in_context",
                "high" if broad else "low",
                "broad exception swallowed without handling"
                if broad
                else "narrow exception deliberately ignored",
            )

    def visit_Assign(self, node):
        self._counter_pattern(node)
        self._concat_pattern(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if (
            self._loop_depth
            and isinstance(node.op, ast.Add)
            and isinstance(node.target, ast.Name)
            and _is_string_like(node.value)
        ):
            self.add(
                "string-concat-in-loop",
                node,
                "violation",
                "medium",
                "string built by repeated += in a loop; collect parts and join",
            )
        self.generic_visit(node)

    def visit_Compare(self, node):
        if (
            self._loop_depth
            and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id in _list_names(self._tree)
        ):
            self.add(
                "membership-in-list-inside-loop",
                node,
                "violation",
                "medium",
                "membership test on a list inside a loop is quadratic; use a set",
            )
        self.generic_visit(node)

    def visit_Return(self, node):
        if isinstance(node.value, ast.Tuple) and len(node.value.elts) >= 4:
            self.add(
                "positional-record",
                node,
                "hint",
                "low",
                "returns a positional tuple of 4+ fields; a named record may model it better",
            )
        self.generic_visit(node)

    def _counter_pattern(self, node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Subscript):
            return
        value = node.value
        if (
            isinstance(value, ast.BinOp)
            and isinstance(value.op, ast.Add)
            and isinstance(value.left, ast.Call)
            and isinstance(value.left.func, ast.Attribute)
            and value.left.func.attr == "get"
            and len(value.left.args) == 2
            and isinstance(value.left.args[1], ast.Constant)
            and value.left.args[1].value == 0
        ):
            self.add(
                "manual-counter",
                node,
                "violation",
                "medium",
                "manual dict counting; collections.Counter or defaultdict(int) expresses this",
            )

    def _concat_pattern(self, node):
        if (
            self._loop_depth
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, ast.Add)
            and isinstance(node.value.left, ast.Name)
            and node.value.left.id == node.targets[0].id
            and isinstance(node.value.right, (ast.List, ast.Tuple))
        ):
            self.add(
                "sequence-concat-in-loop",
                node,
                "violation",
                "medium",
                "x = x + [...] in a loop copies the sequence each time; append or extend",
            )


# --------------------------------------------------------------------- AST helpers


def _is_mutable_default(node):
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        return name in MUTABLE_CALLS
    return False


def _usage(function, name):
    """(mutated, escaped, rebound_before_first_mutation) for a parameter inside a function."""
    mutation_lines = []
    escape_lines = []
    rebind_lines = []
    for node in ast.walk(function):
        if node is function:
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATORS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
        ):
            mutation_lines.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if hasattr(node, "targets") else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _root_name(target) == name:
                    mutation_lines.append(node.lineno)
                elif isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node, ast.AugAssign):
                        mutation_lines.append(node.lineno)
                    else:
                        rebind_lines.append(node.lineno)
                elif isinstance(target, ast.Attribute) and isinstance(node, ast.Assign):
                    if isinstance(node.value, ast.Name) and node.value.id == name:
                        escape_lines.append(node.lineno)
        elif isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:
            if isinstance(node.value, ast.Name) and node.value.id == name:
                escape_lines.append(node.lineno)
    first_activity = min(mutation_lines + escape_lines, default=None)
    rebound_before = bool(
        first_activity is not None and rebind_lines and min(rebind_lines) < first_activity
    )
    return bool(mutation_lines), bool(escape_lines), rebound_before


def _root_name(node):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_open(call):
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "open") or (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and not isinstance(func.value, ast.Constant)
        and isinstance(func.value, ast.Name)
        and func.value.id in {"io", "codecs", "Path"}
    )


def _inside_try_finally(function, call):
    for node in ast.walk(function):
        if isinstance(node, ast.Try) and node.finalbody:
            if any(call is inner for stmt in node.body for inner in ast.walk(stmt)):
                return True
    for node in ast.walk(function):
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                for inner in ast.walk(stmt):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "release"
                    ):
                        return True
    return False


def _handler_names(handler):
    names = set()
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for item in nodes:
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
    return names


def _is_docstring(stmt):
    return isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _is_string_like(node):
    """True when the expression provably yields text.

    Covers the shapes a real ``+=`` accumulator takes: literals, f-strings, ``str``/``repr``
    conversions, ``str.join`` and string concatenation. Anything unproven (an arbitrary call, a
    subscript, a numeric expression) is left to the reviewer rather than guessed at.
    """
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_like(node.left) or _is_string_like(node.right)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in {"str", "repr", "format"}:
            return True
        return name == "join" and bool(node.args)
    return False


def _is_plain_index(loop, use, seq, index):
    for node in ast.walk(loop):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == seq
            and node.slice is use
        ):
            return True
    return False


_LIST_CACHE = {}


def _list_names(tree):
    key = id(tree)
    if key not in _LIST_CACHE:
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
                if isinstance(target, ast.Name) and (
                    isinstance(value, (ast.List, ast.ListComp))
                    or (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "list"
                    )
                ):
                    names.add(target.id)
            if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Subscript):
                base = node.annotation.value
                if isinstance(base, ast.Name) and base.id == "list":
                    names.add(node.arg)
        _LIST_CACHE[key] = names
    return _LIST_CACHE[key]


# ------------------------------------------------------------------------- driver


def scan(root, paths, options):
    files, findings, incomplete = [], [], False
    for rel in paths:
        full = os.path.join(root, rel)
        try:
            with open(full, "rb") as handle:
                source = handle.read()
            tree = ast.parse(source, filename=rel)
        except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as error:
            files.append({"path": rel, "parsed": False, "error": type(error).__name__})
            incomplete = True
            continue
        files.append({"path": rel, "parsed": True, "error": None})
        scanner = Scanner(rel, tree, options)
        scanner.visit(tree)
        findings.extend(scanner.findings)
    findings.sort(key=lambda f: (f["path"], f["line"], f["column"], f["rule"]))
    return {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "typing_expectation": options["typing"],
        "opportunities": sorted(options["opportunities"]),
        "complete": not incomplete,
        "files": files,
        "findings": findings,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_context_scan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--typing-expectation", choices=["required", "none"], default="none")
    parser.add_argument("--opportunities", default="")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv[1:])
    options = {
        "typing": args.typing_expectation,
        "opportunities": {item for item in args.opportunities.split(",") if item},
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
