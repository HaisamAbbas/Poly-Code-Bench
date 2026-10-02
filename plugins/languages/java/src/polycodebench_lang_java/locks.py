"""The frozen Java dependency resolution: the Java analogue of ``Cargo.lock`` (PCB-23-1).

A Java toolchain pins the JDK. It pins nothing about what a task's ``pom.xml`` *resolves to*:
``junit-jupiter:5.10.2`` means "5.10.2 and whatever its own declared dependencies require, as they
resolved on the day it ran". Two runs of the same Maven goal against the same source can therefore
produce different bytecode and different findings without anything visibly changing.

The frozen resolution is what closes that gap. ``mvn -o dependency:list`` is run **once, during
admission**, and its result is recorded in the task package as ``hidden/deps.lock.json``; that file
is a ``config`` input to every Java plan and its canonical digest is part of ``ToolIdentity``.

This module therefore does three things, and refuses the fourth case:

1. parse the frozen resolution and extract the exact ``groupId:artifactId:version`` triples;
2. compute a *canonical* digest over that resolution, so the digest survives re-ordering and
   re-formatting of the file but changes if any version changes;
3. reject a resolution that is not actually pinned - a missing version, or a version range such
   as ``[1.0,)`` - because an unpinned resolution lets the identity drift silently between two runs
   that look identical.

Scope is recorded too (``compile`` / ``test`` / ``provided``), because a task that pulls a test-only
jar into the compile classpath is a different evaluation environment and must not compare equal to
one that does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast

from polycodebench_core.canonical import canonical_digest, parse_json_strict
from polycodebench_core.models import Digest

Scope = Literal["compile", "provided", "runtime", "test"]
LOCK_VERSION = 1
#: A coordinate Maven still has to resolve: a range, a snapshot, or a bare version that carries a
#: version-range marker. Anything here means the resolution is not frozen.
_UNPINNED_MARKERS = ("[", "]", "(", ")", ",SNAPSHOT")


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    """One entry of the frozen resolution."""

    group_id: str
    artifact_id: str
    version: str
    scope: Scope

    @property
    def coordinate(self) -> str:
        return f"{self.group_id}:{self.artifact_id}:{self.version}"

    @property
    def pinned(self) -> bool:
        return bool(self.version) and not any(m in self.version for m in _UNPINNED_MARKERS)


@dataclass(frozen=True, slots=True)
class DependencyLock:
    """A parsed, validated and canonicalised Java dependency resolution."""

    lock_version: int
    artifacts: tuple[ResolvedArtifact, ...]

    def lines(self) -> tuple[str, ...]:
        """Canonical lines: sorted by coordinate, so formatting is not an identity change."""
        return tuple(
            f"{item.coordinate} {item.scope}"
            for item in sorted(self.artifacts, key=lambda i: i.coordinate)
        )

    def canonical(self) -> str:
        return "\n".join([f"lock-version={self.lock_version}", *self.lines()])

    @property
    def digest(self) -> Digest:
        return canonical_digest(self.canonical())

    def coordinates(self) -> tuple[str, ...]:
        return tuple(item.coordinate for item in sorted(self.artifacts, key=lambda i: i.coordinate))

    def test_scope(self) -> tuple[str, ...]:
        """Coordinates on the test classpath only; a candidate can see these, production cannot."""
        return tuple(
            item.coordinate
            for item in sorted(self.artifacts, key=lambda i: i.coordinate)
            if item.scope == "test"
        )


class LockError(ValueError):
    """The lock is absent, unparsable, or not actually pinned."""


def parse_dependency_lock(data: bytes) -> DependencyLock:
    """Parse and canonicalise a frozen Java dependency resolution."""
    try:
        document = parse_json_strict(data)
    except ValueError as error:
        raise LockError(f"dependency lock is not valid JSON: {error}") from None
    if not isinstance(document, dict):
        raise LockError("dependency lock must be a JSON object")
    raw_version = document.get("lock_version")
    if raw_version != LOCK_VERSION:
        raise LockError(f"unsupported dependency lock version: {raw_version!r}")
    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise LockError("dependency lock declares no artifacts")
    artifacts: list[ResolvedArtifact] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw_artifacts:
        if not isinstance(entry, dict):
            raise LockError("dependency lock artifact is not an object")
        group_id = str(entry.get("group_id", ""))
        artifact_id = str(entry.get("artifact_id", ""))
        version = str(entry.get("version", ""))
        scope = str(entry.get("scope", "compile"))
        if not group_id or not artifact_id:
            raise LockError("dependency lock artifact is missing a group or artifact id")
        if scope not in {"compile", "provided", "runtime", "test"}:
            raise LockError(f"unknown dependency scope: {scope!r}")
        key = (group_id, artifact_id)
        if key in seen:
            raise LockError(f"dependency lock declares {group_id}:{artifact_id} twice")
        seen.add(key)
        item = ResolvedArtifact(group_id, artifact_id, version, cast("Scope", scope))
        if not item.pinned:
            raise LockError(
                f"dependency {group_id}:{artifact_id} is not pinned: version {version!r}"
            )
        artifacts.append(item)
    return DependencyLock(LOCK_VERSION, tuple(artifacts))


def lock_digest(data: bytes) -> Digest:
    """Canonical digest of a frozen Java dependency resolution."""
    return parse_dependency_lock(data).digest


def lock_document(lock: DependencyLock) -> str:
    """Re-serialise a parsed lock in canonical form (used by the admission tooling)."""
    return json.dumps(
        {
            "lock_version": lock.lock_version,
            "artifacts": [
                {
                    "group_id": item.group_id,
                    "artifact_id": item.artifact_id,
                    "version": item.version,
                    "scope": item.scope,
                }
                for item in sorted(lock.artifacts, key=lambda i: i.coordinate)
            ],
        },
        sort_keys=True,
        indent=2,
    )
