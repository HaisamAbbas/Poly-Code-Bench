"""Evaluation assignment boundaries for frozen task archives and hidden inputs."""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from polycodebench_core.models import TaskVersion
from polycodebench_orchestration.grading.assignment import (
    DatabaseEvaluationAssignmentLoader,
    EvaluationAssignmentRejected,
    _archive_files,
    _verify_admin_manifest,
    freeze_task_view,
)
from polycodebench_plugins_api import (
    ExecutableLanguagePlugin,
    FrozenTask,
    TaskDraft,
    load_allowlist,
    load_language_plugin,
)
from polycodebench_services.task_packages import TaskPackageImporter
from test_task_admission_postgres import _task_document

ROOT = Path(__file__).resolve().parents[1]
TASK_PACKAGE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"


def _zip(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, body, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, body)
    return stream.getvalue()


def test_task_bundle_archive_requires_safe_canonical_regular_files() -> None:
    assert _archive_files(_zip([("visible/repo/main.py", b"pass", stat.S_IFREG | 0o600)])) == {
        "visible/repo/main.py": b"pass"
    }
    with pytest.raises(ValueError, match="unsafe path"):
        _archive_files(_zip([("visible/../secret.py", b"private", None)]))
    with pytest.raises(ValueError, match="symbolic link"):
        _archive_files(_zip([("visible/repo/link", b"../secret", stat.S_IFLNK | 0o777)]))
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = _zip(
            [
                ("visible/repo/main.py", b"first", None),
                ("visible/repo/main.py", b"second", None),
            ]
        )
    with pytest.raises(ValueError, match="duplicated"):
        _archive_files(duplicate)


def test_plugin_view_uses_frozen_task_contract_and_only_declared_hidden_overlays() -> None:
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    visible = _archive_files(imported.visible_archive)
    hidden = _archive_files(imported.hidden_archive)
    task = _task_document(
        task_id="assignment-python-fixture",
        report_digest="sha256:" + "a" * 64,
        visible_id=uuid4(),
        hidden_id=uuid4(),
        imported=imported,
    )
    _verify_admin_manifest(hidden, task)
    allowlist = load_allowlist(ROOT / "config" / "plugins" / "allowlist-v1.yaml")
    plugin = cast(ExecutableLanguagePlugin, load_language_plugin(allowlist, "python"))
    view = freeze_task_view(
        task,
        {**visible, **hidden},
        "sha256:" + hashlib.sha256(b"frozen task record").hexdigest(),
        plugin,
    )
    overlays = DatabaseEvaluationAssignmentLoader._declared_overlay_files(view, hidden)

    declared = {
        path
        for group in view.inventory["groups"]
        for path in group["files"]
    }
    quality = view.quality
    performance = quality.get("performance")
    if isinstance(performance, dict):
        declared.add(performance["workload_file"])
    assert set(overlays) == declared
    assert overlays
    assert "hidden/oracle.json" not in overlays
    assert "hidden/quality-plan.yaml" not in overlays
    assert not any(path.startswith("hidden/reference/") for path in overlays)
    assert not any(path.startswith("admission/") for path in overlays)


def test_freeze_task_view_rejects_plugin_changes_to_registered_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    task = _task_document(
        task_id="assignment-python-fixture",
        report_digest="sha256:" + "a" * 64,
        visible_id=uuid4(),
        hidden_id=uuid4(),
        imported=imported,
    )
    allowlist = load_allowlist(ROOT / "config" / "plugins" / "allowlist-v1.yaml")
    plugin = cast(ExecutableLanguagePlugin, load_language_plugin(allowlist, "python"))
    original_freeze = plugin.freeze_view

    def mismatched_freeze(
        draft: TaskDraft, task_digest: str, task_version: int = 1
    ) -> FrozenTask:
        return original_freeze(draft, task_digest, task_version).model_copy(
            update={"task_id": "different-task"}
        )

    monkeypatch.setattr(plugin, "freeze_view", mismatched_freeze)

    with pytest.raises(EvaluationAssignmentRejected, match="differs from the frozen task record"):
        freeze_task_view(
            task.model_copy(update={"task_id": "other-task"}),
            {
                **_archive_files(imported.visible_archive),
                **_archive_files(imported.hidden_archive),
            },
            "sha256:" + hashlib.sha256(b"frozen task record").hexdigest(),
            plugin,
        )


def test_admin_manifest_must_match_frozen_database_contract() -> None:
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    hidden = _archive_files(imported.hidden_archive)
    task = _task_document(
        task_id="assignment-python-fixture",
        report_digest="sha256:" + "a" * 64,
        visible_id=uuid4(),
        hidden_id=uuid4(),
        imported=imported,
    )
    mismatched = task.model_copy(
        update={"runtime": task.runtime.model_copy(update={"image_digest": "sha256:" + "b" * 64})}
    )
    with pytest.raises(EvaluationAssignmentRejected, match="differs from its frozen database"):
        _verify_admin_manifest(hidden, mismatched)


def test_admin_manifest_rejects_duplicate_yaml_keys() -> None:
    with pytest.raises(EvaluationAssignmentRejected, match="admin manifest is invalid"):
        _verify_admin_manifest(
            {"administrative-manifest.yaml": b"kind: task_package\nkind: altered\n"},
            cast(TaskVersion, object()),
        )
