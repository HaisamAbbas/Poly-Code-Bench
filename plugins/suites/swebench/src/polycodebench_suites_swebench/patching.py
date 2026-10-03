"""A typed boundary over the repository's untyped patch engine.

``polycodebench_runner.guest_helper`` ships as plain stdlib source into the sandbox images, so it
carries no type information (see the ``mypy`` override in ``pyproject.toml``). Three modules need
its parse/apply behaviour, and importing it directly puts an untyped surface into typed code three
times over.

This module is the single place that does so. It also fixes the semantics the native suite depends
on, in one spot rather than three: an empty patch is a legitimate candidate submission (it touches
nothing, so it cannot escape an allowlist, and the resolution measure grades it unresolved), and a
patch is applied wholly or not at all - a hunk that does not apply raises instead of leaving a
partial tree.
"""

from __future__ import annotations

from typing import NamedTuple

from polycodebench_runner.guest_helper import ToolFailure, apply_hunks, parse_patch


class PatchRejected(ValueError):
    """A patch cannot be parsed, or a hunk does not apply to the content it addresses."""


class FileChange(NamedTuple):
    """One file a patch addresses, with the hunks to apply to its current content."""

    path: str
    hunks: object
    removed: bool


def parse(patch: str) -> tuple[FileChange, ...]:
    """Parse a unified diff into the files it addresses, or raise.

    An empty patch parses to no changes: it is a submission that changed nothing, not a malformed
    one. Renames, mode changes and binary patches are refused by the engine, as are NUL bytes and
    paths that escape the tree.
    """
    if not patch.strip():
        return ()
    try:
        parsed = parse_patch(patch)  # type: ignore[no-untyped-call]
    except ToolFailure as error:
        if "no file changes" in str(error):
            return ()
        raise PatchRejected(f"patch rejected: {error}") from error
    changes: list[FileChange] = []
    for file_patch in parsed:
        new_path = getattr(file_patch, "new", None)
        old_path = getattr(file_patch, "old", None)
        path = new_path or old_path
        if path is None:
            raise PatchRejected("patch header names no path")
        changes.append(
            FileChange(
                path=str(path),
                hunks=getattr(file_patch, "hunks", None),
                removed=new_path is None,
            )
        )
    return tuple(changes)


def apply_one(content: bytes, change: FileChange) -> bytes | None:
    """Apply one file's hunks; ``None`` means the file is deleted by this change."""
    if change.removed:
        return None
    try:
        updated = apply_hunks(  # type: ignore[no-untyped-call]
            content.decode("utf-8"), change.hunks, change.path
        )
    except (ToolFailure, UnicodeDecodeError) as error:
        raise PatchRejected(f"{change.path}: patch does not apply: {error}") from error
    return str(updated).encode("utf-8")


def apply_to_tree(files: dict[str, bytes], patch: str) -> dict[str, bytes]:
    """Apply every change in ``patch`` to a flat path -> bytes tree, wholly or not at all."""
    tree = dict(files)
    for change in parse(patch):
        updated = apply_one(tree.get(change.path, b""), change)
        if updated is None:
            del tree[change.path]
            continue
        tree[change.path] = updated
    return tree


def targets(patch: str) -> tuple[str, ...]:
    """The distinct paths a patch addresses, sorted."""
    return tuple(sorted({change.path for change in parse(patch)}))


def is_empty(patch: str) -> bool:
    """Whether a patch addresses nothing at all."""
    return not parse(patch)
