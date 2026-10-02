"""package-lock.json identity: the second half of the JavaScript/TypeScript evaluator identity.

A Node image pins the *runtime*. It does not pin what a package resolves to: two runs of the same
``vitest`` against the same source can install different trees if the registry moved between them.
``package-lock.json`` is what freezes that, so its content is part of the identity of any tool whose
result depends on the install (``ToolIdentity.lock_digest``). Two results are comparable only when
the image digest, the Node version and this digest all match.

This module therefore does three things, and refuses the fourth case:

1. parse ``package-lock.json`` and extract the exact resolution (``name``/``version``/``integrity``);
2. compute a *canonical* digest over that resolution, so the digest is stable across formatting,
   key ordering and package ordering in the file, but changes if any resolution changes;
3. reject a lock that is not actually pinned, because an unpinned lock would let the identity drift
   silently between two runs that look identical.

A lock that is not actually pinned is not a lock: a registry dependency with no ``integrity`` is
verified by nothing, and a lock with no ``lockfileVersion`` or no packages at all pins nothing.

The canonical form is sorted by (name, version) and uses the integrity hash when npm recorded one,
so re-ordering or re-formatting the file is not a spurious identity change while a real dependency
change is.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from polycodebench_core.models import Digest

# The root project entry of ``packages`` is keyed by the empty string and carries the workspace
# itself, not a dependency, so it is skipped rather than reported as an unnamed package.
_ROOT_KEY = ""
_MARKER = "node_modules/"


@dataclass(frozen=True, slots=True)
class ResolvedPackage:
    """One entry of the frozen resolution."""

    name: str
    version: str
    integrity: str | None
    source: str | None

    @property
    def is_path_dependency(self) -> bool:
        """A path dependency is local to the task, so it carries no registry integrity hash."""
        return self.source is None


@dataclass(frozen=True, slots=True)
class PackageLock:
    """A parsed, validated and canonicalised ``package-lock.json``."""

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


def _package_name(key: str) -> str:
    """The package's own name: npm keys a dependency by the path that reaches it."""
    return key.rsplit(_MARKER, 1)[-1]


def parse_package_lock(data: bytes) -> PackageLock:
    """Parse and canonicalise a ``package-lock.json``.

    Raises :class:`LockError` when the lock cannot be trusted to fix the resolution: a file that
    does not declare a ``lockfileVersion``, an empty ``packages`` map, a registry package with no
    ``integrity``, or the same ``(name, version)`` resolved twice.
    """
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LockError(f"package-lock.json is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LockError("package-lock.json is not a JSON object")

    raw_version = document.get("lockfileVersion")
    if not isinstance(raw_version, int):
        raise LockError("package-lock.json does not declare an integer `lockfileVersion`")

    entries = document.get("packages")
    if not isinstance(entries, dict) or not entries:
        raise LockError("package-lock.json declares no packages, so nothing is pinned")

    packages: list[ResolvedPackage] = []
    for key, entry in entries.items():
        if key == _ROOT_KEY:
            continue
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise LockError("package-lock.json has a malformed packages entry")
        name = _package_name(key)
        if not name:
            raise LockError(f"package-lock.json has a package with no name at {key!r}")
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            raise LockError(f"package-lock.json package {name!r} has no version")
        integrity = entry.get("integrity")
        source = entry.get("resolved")
        if integrity is not None and not isinstance(integrity, str):
            raise LockError(f"package-lock.json package {name!r} has a malformed integrity")
        if source is not None and not isinstance(source, str):
            raise LockError(f"package-lock.json package {name!r} has a malformed resolved URL")
        # A package resolved from a registry must carry npm's integrity hash; without it the
        # contents are not verified and the resolution could differ between runs. A link/workspace
        # entry carries no URL and is local to the task, so it needs no hash.
        if isinstance(source, str) and source.startswith(("http:", "https:")) and not integrity:
            raise LockError(
                f"package-lock.json package {name!r} {version} comes from {source} with no "
                "integrity; the resolution is not pinned"
            )
        packages.append(ResolvedPackage(name, version, integrity, source))

    if not packages:
        raise LockError("package-lock.json declares no dependency packages, so nothing is pinned")

    # Duplicate name/version pairs would make the resolution ambiguous: two entries claiming the
    # same resolution is a lock npm itself would not write.
    seen = [(item.name, item.version) for item in packages]
    if len(set(seen)) != len(seen):
        duplicates = sorted({pair for pair in seen if seen.count(pair) > 1})
        raise LockError(f"package-lock.json pins the same package twice: {duplicates}")

    packages.sort(key=lambda item: (item.name, item.version))
    lines = [f"lock-version={raw_version}"]
    lines += [
        f"{item.name} {item.version} {item.integrity or 'path'}" for item in packages
    ]
    return PackageLock(raw_version, tuple(packages), "\n".join(lines))


def lock_digest(data: bytes) -> Digest:
    """Canonical digest of a ``package-lock.json``'s resolution."""
    return parse_package_lock(data).digest


__all__ = [
    "LockError",
    "PackageLock",
    "ResolvedPackage",
    "lock_digest",
    "parse_package_lock",
]
