"""Inputs to a solve session: everything a model is allowed to see, and nothing else."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from polycodebench_core.model_contracts import CallScope, Strict
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import Slug, TaskOutputContract
from polycodebench_core.solve_contracts import (
    EffectiveProtocol,
    PathForbidden,
    normalize_workspace_path,
)
from pydantic import Field, ValidationError

VISIBLE_PREFIX = "visible/"
VISIBLE_MANIFEST = "visible-manifest.json"
PUBLIC_TESTS_FILE = "public-tests.json"
MAX_VISIBLE_BYTES = 256 * 1024**2


class PublicTestGroup(Strict):
    """A test group published with the task. Its command is fixed by the task, not the model."""

    id: Slug
    argv: Annotated[list[str], Field(min_length=1, max_length=64)]
    cwd: str = "."
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30


@dataclass(frozen=True)
class SolveAssignment:
    attempt_id: UUID
    scope: CallScope
    effective: EffectiveProtocol
    config: ModelConfig
    config_document_id: UUID
    sample_seed: int
    instructions: str
    contract: TaskOutputContract
    required_outputs: list[str]
    protected_paths: list[str]
    visible_files: Mapping[str, bytes]  # workspace-relative paths
    base_digest: str  # digest of the visible bundle the workspace was built from
    public_groups: Mapping[str, PublicTestGroup] = field(default_factory=dict)
    # Identity of hidden material (paths, digests, ids). No request may contain any of them.
    forbidden_markers: tuple[bytes, ...] = ()

    def protected_baseline(self) -> dict[str, bytes]:
        return {
            path: data
            for path, data in self.visible_files.items()
            if any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.protected_paths)
        }


def workspace_from_visible_archive(archive: bytes) -> dict[str, bytes]:
    """Workspace files from a visible-bundle zip (``visible/...``); manifest metadata is dropped.

    Every entry is checked: only regular files beneath ``visible/`` with safe relative paths.
    """
    files: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for info in bundle.infolist():
            if info.is_dir() or info.filename == VISIBLE_MANIFEST:
                continue
            if not info.filename.startswith(VISIBLE_PREFIX):
                raise ValueError("visible bundle contains an entry outside visible/")
            try:
                path = normalize_workspace_path(info.filename[len(VISIBLE_PREFIX) :])
            except PathForbidden as error:
                raise ValueError("visible bundle contains an unsafe path") from error
            total += info.file_size
            if total > MAX_VISIBLE_BYTES or path in files:
                raise ValueError("visible bundle is too large or has duplicate paths")
            files[path] = bundle.read(info)
    return files


def public_groups_from_workspace(files: Mapping[str, bytes]) -> dict[str, PublicTestGroup]:
    """Parse ``public-tests.json`` if the task publishes one; absent means no public tests."""
    raw = files.get(PUBLIC_TESTS_FILE)
    if raw is None:
        return {}
    try:
        document = json.loads(raw)
        groups = [PublicTestGroup.model_validate(item, strict=False) for item in document["groups"]]
    except (ValueError, KeyError, TypeError, ValidationError) as error:
        raise ValueError("public-tests.json is malformed") from error
    if len({group.id for group in groups}) != len(groups):
        raise ValueError("public-tests.json repeats a group id")
    return {group.id: group for group in groups}
