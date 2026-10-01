"""Cargo.lock identity: the second half of the Rust evaluator identity (PCB-11-1).

A Rust toolchain pins the *compiler*. It does not pin what a crate resolves to: two runs of the
same ``cargo test`` against the same source can produce different artefacts if the dependency
graph moved. ``Cargo.lock`` is what freezes that, so its content is part of the identity of any
tool whose result depends on the build (``ToolIdentity.lock_digest``). Two results are comparable
only when the image digest, the toolchain and this digest all match.

This module therefore does three things, and refuses the fourth case:

1. parse ``Cargo.lock`` and extract the exact resolution (``name``/``version``/``checksum``);
2. compute a *canonical* digest over that resolution, so the digest is stable across formatting,
   key ordering and package ordering in the file, but changes if any resolution changes;
3. reject a lock that is not actually pinned, because an unpinned lock would let the identity
   drift silently between two runs that look identical.

The canonical form is sorted by (name, version) and uses the checksum when cargo recorded one, so
re-ordering or re-formatting the file is not a spurious identity change while a real dependency
change is.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass

from polycodebench_core.models import Digest


@dataclass(frozen=True, slots=True)
class ResolvedPackage:
    """One entry of the frozen resolution."""

    name: str
    version: str
    checksum: str | None
    source: str | None

    @property
    def is_path_dependency(self) -> bool:
        """A path dependency is local to the task, so it carries no registry checksum."""
        return self.source is None


@dataclass(frozen=True, slots=True)
class CargoLock:
    """A parsed, validated and canonicalised ``Cargo.lock``."""

    lock_version: int
    packages: tuple[ResolvedPackage, ...]
    canonical: str

    @property
    def digest(self) -> Digest:
        return "sha256:" + hashlib.sha256(self.canonical.encode()).hexdigest()

    def resolutions(self) -> tuple[str, ...]:
        return tuple(f"{item.name} {item.version}" for item in self.packages)


class LockError(ValueError):
    """The lock is absent, unparsable, or not actually pinned."""


def parse_cargo_lock(data: bytes) -> CargoLock:
    """Parse and canonicalise a ``Cargo.lock``.

    Raises :class:`LockError` when the lock cannot be trusted to fix the resolution: a file that
    does not declare a version, an empty package list, or a registry package with no checksum.
    """
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LockError(f"Cargo.lock is not valid TOML: {error}") from error

    raw_version = document.get("version")
    if not isinstance(raw_version, int):
        raise LockError("Cargo.lock does not declare an integer `version`")

    entries = document.get("package")
    if not isinstance(entries, list) or not entries:
        raise LockError("Cargo.lock declares no packages, so nothing is pinned")

    packages: list[ResolvedPackage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise LockError("Cargo.lock has a malformed [[package]] entry")
        name, version = entry.get("name"), entry.get("version")
        if not isinstance(name, str) or not name:
            raise LockError("Cargo.lock has a package without a name")
        if not isinstance(version, str) or not version:
            raise LockError(f"Cargo.lock package {name!r} has no version")
        checksum = entry.get("checksum")
        source = entry.get("source")
        if checksum is not None and not isinstance(checksum, str):
            raise LockError(f"Cargo.lock package {name!r} has a malformed checksum")
        if source is not None and not isinstance(source, str):
            raise LockError(f"Cargo.lock package {name!r} has a malformed source")
        # A package resolved from a registry must carry cargo's checksum; without it the
        # contents are not verified and the resolution could differ between runs.
        if isinstance(source, str) and not checksum:
            raise LockError(
                f"Cargo.lock package {name!r} {version} comes from {source} with no checksum; "
                "the resolution is not pinned"
            )
        packages.append(ResolvedPackage(name, version, checksum, source))

    # Duplicate name/version pairs would make the resolution ambiguous.
    seen = [(item.name, item.version) for item in packages]
    if len(set(seen)) != len(seen):
        duplicates = sorted({pair for pair in seen if seen.count(pair) > 1})
        raise LockError(f"Cargo.lock pins the same package twice: {duplicates}")

    packages.sort(key=lambda item: (item.name, item.version))
    lines = [f"lock-version={raw_version}"]
    lines += [f"{item.name} {item.version} {item.checksum or 'path'}" for item in packages]
    return CargoLock(raw_version, tuple(packages), "\n".join(lines))


def lock_digest(data: bytes) -> Digest:
    """Canonical digest of a ``Cargo.lock``'s resolution."""
    return parse_cargo_lock(data).digest
