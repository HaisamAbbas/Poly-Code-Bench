"""Pure task-set split and exposure policy helpers."""

from __future__ import annotations

from collections.abc import Iterable

from polycodebench_core.canonical import canonical_digest


def package_snapshot_digest(manifest_digest: str, visible_digest: str, hidden_digest: str) -> str:
    """Bind admission to the manifest and both immutable bundle snapshots."""
    return canonical_digest(
        {
            "schema_version": 1,
            "kind": "task_package_snapshot",
            "manifest_digest": manifest_digest,
            "visible_digest": visible_digest,
            "hidden_digest": hidden_digest,
        }
    )


def validate_cluster_split_assignments(assignments: Iterable[tuple[str, str]]) -> None:
    """Reject a cluster assigned to more than one designated split."""
    split_by_cluster: dict[str, str] = {}
    for cluster_id, split in assignments:
        prior = split_by_cluster.setdefault(cluster_id, split)
        if prior != split:
            raise ValueError("related task variants cannot be assigned to different splits")
