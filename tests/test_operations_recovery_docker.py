"""Prompt 33 restore-rehearsal negative control (Docker-gated).

A database restored without one of its referenced artifacts must NOT count as a recovery
(T 22.6): the rehearsal has to fail at digest integrity, report no recovery time, and still
destroy the isolated environment. Requires a backup produced by
``pcb-ops backup create`` (path in PCB_OPS_REHEARSAL_BACKUP) and PCB_TEST_DOCKER=1.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from polycodebench_operations.recovery import rehearse_restore
from polycodebench_operations.rehearsal_data import BUNDLE_KIND

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1" or not os.environ.get("PCB_OPS_REHEARSAL_BACKUP"),
    reason="needs Docker and a rehearsal backup (PCB_TEST_DOCKER=1, PCB_OPS_REHEARSAL_BACKUP)",
)


def test_restore_missing_a_referenced_artifact_is_not_a_recovery(tmp_path: Path) -> None:
    source = Path(os.environ["PCB_OPS_REHEARSAL_BACKUP"])
    backup = tmp_path / "backup"
    shutil.copytree(source, backup)
    manifest = json.loads((backup / "backup-manifest.json").read_text("utf-8"))
    # Drop one scorecard archive bundle from the copy entirely (consistently, so the backup's
    # own file digests still verify): the object store copy is now incomplete.
    victim = next(
        item
        for item in manifest["objects"]
        if item["visibility"] == "internal"
        and f'"kind":"{BUNDLE_KIND}"'
        in (backup / "objects" / "internal" / item["key"]).read_text("utf-8", errors="ignore")
    )
    (backup / "objects" / "internal" / victim["key"]).unlink()
    manifest["objects"] = [item for item in manifest["objects"] if item is not victim]
    (backup / "backup-manifest.json").write_text(json.dumps(manifest), "utf-8")

    report = rehearse_restore(backup, work_dir=tmp_path / "work")

    assert report["passed"] is False and report["recovery_time_seconds"] is None
    failed = [step for step in report["steps"] if step["status"] == "failed"]
    assert [step["step"] for step in failed] == ["artifact digest integrity"]
    assert failed[0]["missing_objects"] == 1
    assert report["resources_reclaimed"] is True
