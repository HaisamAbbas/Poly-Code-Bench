"""Go module identity: ``go.mod``/``go.sum`` are the second half of the Go evaluator identity.

A Go toolchain pins the *compiler*. It does not pin what a module resolves to: ``go.mod`` declares
the requirements and ``go.sum`` records the content hashes the module graph resolved to. Two runs of
the same source can therefore produce different artefacts if the graph moved, so the pair is part
of the identity of any tool whose result depends on the build (``ToolIdentity.lock_digest``).

This module parses both files, canonicalises the resolution, and refuses the fourth case:

1. parse ``go.mod`` into its module path, language version and requirements (direct and indirect);
2. parse ``go.sum`` into the set of ``module@version`` entries it verifies;
3. compute a *canonical* digest over the resolution, stable across formatting, key ordering and
   block layout, but different if any resolution or hash changes;
4. reject a graph that is not actually pinned - a required module with no ``go.sum`` hash, an empty
   requirement set where dependencies are declared, or a duplicate version - because an unpinned
   graph lets the identity drift between two runs that look identical.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from polycodebench_core.models import Digest

_MODULE = re.compile(r"^module\s+(?P<path>\S+)\s*$")
_GO_VERSION = re.compile(r"^(?:go|toolchain)\s+(?P<version>\S+)\s*$")
_REQUIRE_BLOCK = re.compile(r"^require\s*\($")
_REQUIRE_LINE = re.compile(
    r"^(?P<path>[A-Za-z0-9._~/\-]+\.[A-Za-z0-9._~/\-]+)\s+(?P<version>v\S+)(?:\s+//\s*indirect)?\s*$"
)
_SUM = re.compile(r"^(?P<path>\S+)\s+(?P<version>v\S+?)(?P<mod>/go\.mod)?\s+h1:[A-Za-z0-9+/=]+")


@dataclass(frozen=True, slots=True)
class RequiredModule:
    """One entry of the frozen resolution."""

    path: str
    version: str
    indirect: bool


@dataclass(frozen=True, slots=True)
class GoModule:
    """A parsed, validated and canonicalised ``go.mod``/``go.sum`` pair."""

    module: str
    go_version: str
    requires: tuple[RequiredModule, ...]
    sums: frozenset[tuple[str, str]]
    canonical: str

    @property
    def digest(self) -> Digest:
        return "sha256:" + hashlib.sha256(self.canonical.encode()).hexdigest()

    def resolutions(self) -> tuple[str, ...]:
        return tuple(f"{item.path} {item.version}" for item in self.requires)


class LockError(ValueError):
    """The module graph is absent, unparsable, or not actually pinned."""


def parse_module_files(mod: bytes, sums: bytes | None) -> GoModule:
    """Parse and canonicalise a ``go.mod``/``go.sum`` pair.

    Raises :class:`LockError` when the resolution cannot be trusted to be fixed.
    """
    try:
        text = mod.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LockError(f"go.mod is not valid UTF-8: {error}") from error

    module = None
    go_version = None
    requires: list[RequiredModule] = []
    in_block = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if _REQUIRE_BLOCK.match(line):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if in_block:
            match = _REQUIRE_LINE.match(line)
            if match is None:
                raise LockError(f"go.mod line {number} is not a valid requirement: {line!r}")
            requires.append(
                RequiredModule(match.group("path"), match.group("version"), "indirect" in raw)
            )
            continue
        if line.startswith("require "):
            match = _REQUIRE_LINE.match(line.removeprefix("require ").strip())
            if match is None:
                raise LockError(f"go.mod line {number} is not a valid requirement: {line!r}")
            requires.append(
                RequiredModule(match.group("path"), match.group("version"), "indirect" in raw)
            )
            continue
        if module is None and (match := _MODULE.match(line)):
            module = match.group("path")
            continue
        if go_version is None and (match := _GO_VERSION.match(line)):
            go_version = match.group("version")

    if module is None:
        raise LockError("go.mod does not declare a `module` path")
    if go_version is None:
        raise LockError("go.mod does not declare a `go` version, so the language level is unpinned")
    if any(item.path == module for item in requires):
        raise LockError(f"go.mod requires its own module {module!r}")

    seen = [(item.path, item.version) for item in requires]
    if len(set(seen)) != len(seen):
        duplicates = sorted({pair for pair in seen if seen.count(pair) > 1})
        raise LockError(f"go.mod requires the same module twice: {duplicates}")

    verified: set[tuple[str, str]] = set()
    if sums is not None:
        try:
            sum_text = sums.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LockError(f"go.sum is not valid UTF-8: {error}") from error
        for line in sum_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = _SUM.match(stripped)
            if match is not None and match.group("mod") is None:
                # Only the module zip hash fixes the content; the /go.mod hash alone does not.
                verified.add((match.group("path"), match.group("version")))

    missing = [f"{item.path} {item.version}" for item in requires if (item.path, item.version) not in verified]
    if missing:
        raise LockError(
            "go.sum has no content hash for the required modules "
            f"{sorted(missing)}; the resolution is not pinned"
        )

    requires.sort(key=lambda item: (item.path, item.version))
    lines = [f"module={module}", f"go={go_version}"]
    lines += [f"require {item.path} {item.version} {item.indirect}" for item in requires]
    lines += [f"sum {path} {version}" for path, version in sorted(verified)]
    return GoModule(module, go_version, tuple(requires), frozenset(verified), "\n".join(lines))


def module_digest(mod: bytes, sums: bytes | None) -> Digest:
    """Canonical digest of a ``go.mod``/``go.sum`` resolution."""
    return parse_module_files(mod, sums).digest
