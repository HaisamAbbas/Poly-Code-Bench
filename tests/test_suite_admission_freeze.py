"""Freezing tasks on development-sandbox suite admission evidence (no database or Docker).

The suite reports here are test data shaped like ``SuiteAdmission`` output; the binding rules
under test are the ones ``PostgresTaskRepository`` and ``pcb task freeze`` apply.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import TaskVersion
from polycodebench_persistence.tasks import (
    SPLITS_BY_EXECUTION_TIER,
    SUITE_ADMISSION_CHECK_IDS,
    PostgresTaskRepository,
)
from polycodebench_plugins_api.admission import SuiteAdmissionReport
from polycodebench_services.task_packages import ImportedTaskPackage, TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "taskpacks" / "admission-smoke"


def _load_pcb() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pcb_under_test", ROOT / "scripts" / "pcb.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pcb = _load_pcb()


@pytest.fixture(scope="module")
def imported() -> ImportedTaskPackage:
    return TaskPackageImporter().import_package(PACKAGE)


def _with_digest(body: dict[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in body.items() if key != "report_digest"}
    return {**content, "report_digest": canonical_digest(content)}


def _suite_report(imported: ImportedTaskPackage, **changes: Any) -> dict[str, Any]:
    check_ids = (*SUITE_ADMISSION_CHECK_IDS.values(), "timeout-variants-rejected")
    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "suite_admission_report",
        "profile_id": "admission-v1",
        "task_id": imported.manifest.task.task_id,
        "task_version": imported.manifest.task.version,
        "package_digest": imported.package_digest,
        "plugin_id": "python",
        "execution_tier": "development_sandbox",
        "image_digests": [imported.manifest.runtime.image_digest, "sha256:" + "e" * 64],
        "checks": [
            {
                "schema_version": 1,
                "kind": "admission_check",
                "check_id": check_id,
                "status": "pass",
                "detail": "",
            }
            for check_id in check_ids
        ],
        "runs": [
            {
                "schema_version": 1,
                "kind": "variant_run",
                "name": "reference",
                "variant": "reference",
                "repetitions": 5,
                "gates": ["pass"] * 5,
                "failing_cases": [],
                "reasons": [],
                "outcome_digests": ["sha256:" + "d" * 64] * 5,
                "analyzer_scans": [],
                "issue_families": [],
                "durations_ms": [10, 11, 12, 13, 14],
                "quality_only_pass": None,
            }
        ],
        "executable_admission_passed": True,
        "pending_gates": ["production-worker-execution-tier"],
        "quality_admission": "pending",
    }
    body.update(changes)
    return _with_digest(body)


def _document(imported: ImportedTaskPackage, report: dict[str, Any]) -> TaskVersion:
    manifest = imported.manifest
    statuses = {check["check_id"]: check["status"] for check in report["checks"]}
    task_document = {
        "schema_version": 1,
        "kind": "task_version",
        **manifest.task.model_dump(mode="json", exclude={"methodology_label"}),
        "source": manifest.source.model_dump(mode="json"),
        "visible_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": str(uuid4()),
            "digest": imported.visible_digest,
            "visibility": "internal",
        },
        "hidden_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": str(uuid4()),
            "digest": imported.hidden_digest,
            "visibility": "hidden",
        },
        "output_contract_digest": manifest.task.output_contract_digest,
        "output_contract": manifest.output_contract.model_dump(mode="json"),
        "runtime": manifest.runtime.model_dump(mode="json"),
        "acceptance": manifest.acceptance.model_dump(mode="json"),
        "quality_plan": manifest.quality_plan.model_dump(mode="json"),
        "oracle": manifest.oracle.model_dump(mode="json"),
        "protocol_constraints": manifest.protocol_constraints.model_dump(mode="json"),
        "admission_report": {
            "schema_version": 1,
            "kind": "admission_report",
            "profile_id": "admission-v1",
            "report_digest": report["report_digest"],
            "execution_tier": "development_sandbox",
            **{
                f"{name}_check": statuses.get(check_id, "pass")
                for name, check_id in SUITE_ADMISSION_CHECK_IDS.items()
            },
            "reviewer_id": None,
            "reviewed_at": None,
        },
    }
    return TaskVersion.model_validate_json(json.dumps(task_document))


def test_matching_suite_evidence_binds_to_the_frozen_snapshot(
    imported: ImportedTaskPackage,
) -> None:
    report = _suite_report(imported)
    PostgresTaskRepository._validate_admission(
        _document(imported, report), report, imported.manifest_digest
    )


def test_suite_evidence_is_never_scored_or_held_out() -> None:
    assert SPLITS_BY_EXECUTION_TIER["development_sandbox"] == {"fixture", "public_development"}
    assert SPLITS_BY_EXECUTION_TIER["local_fixture"] == {"fixture"}
    assert "production_worker" not in SPLITS_BY_EXECUTION_TIER


@pytest.mark.parametrize(
    "mutation",
    ["failed_check", "missing_check", "snapshot", "image", "task", "tampered", "kind"],
)
def test_unbound_or_failing_suite_evidence_is_refused(
    imported: ImportedTaskPackage, mutation: str
) -> None:
    report = _suite_report(imported)
    document = _document(imported, report)
    if mutation == "failed_check":
        report["checks"][-1]["status"] = "fail"
        report["executable_admission_passed"] = False
    elif mutation == "missing_check":
        report["checks"] = [
            check for check in report["checks"] if check["check_id"] != "known-fault-rejection"
        ]
    elif mutation == "snapshot":
        report["package_digest"] = "sha256:" + "f" * 64
    elif mutation == "image":
        report["image_digests"] = ["sha256:" + "e" * 64]
    elif mutation == "task":
        report["task_version"] = imported.manifest.task.version + 1
    elif mutation == "kind":
        report["kind"] = "admission_execution_report"
    if mutation != "tampered":
        report = _with_digest(report)
    else:
        report["runs"][0]["gates"] = ["fail"] * 5
    document = _document(imported, report) if mutation != "tampered" else document
    with pytest.raises(ValueError):
        PostgresTaskRepository._validate_admission(document, report, imported.manifest_digest)


def test_pcb_freeze_accepts_suite_evidence_only_when_the_replay_matches(
    imported: ImportedTaskPackage, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _suite_report(imported)
    replay = _suite_report(
        imported, runs=[{**stored["runs"][0], "durations_ms": [20, 21, 22, 23, 24]}]
    )
    monkeypatch.setattr(
        pcb,
        "_replay_suite_admission",
        lambda package, plugin_id: SuiteAdmissionReport.model_validate_json(json.dumps(replay)),
    )
    evidence, summary = pcb._suite_evidence(PACKAGE, imported, stored)
    assert evidence["report_digest"] == stored["report_digest"] == summary.report_digest
    assert summary.execution_tier == "development_sandbox"
    assert summary.profile_id == "admission-v1"

    diverged = _suite_report(imported, runs=[{**stored["runs"][0], "gates": ["fail"] * 5}])
    monkeypatch.setattr(
        pcb,
        "_replay_suite_admission",
        lambda package, plugin_id: SuiteAdmissionReport.model_validate_json(json.dumps(diverged)),
    )
    with pytest.raises(ValueError, match="replay"):
        pcb._suite_evidence(PACKAGE, imported, stored)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"execution_tier": "production_worker"}, "trusted worker"),
        ({"package_digest": "sha256:" + "f" * 64}, "does not belong"),
        ({"image_digests": ["sha256:" + "e" * 64]}, "runtime image"),
    ],
)
def test_pcb_freeze_refuses_suite_evidence_before_replaying(
    imported: ImportedTaskPackage,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
    message: str,
) -> None:
    def no_replay(package: Path, plugin_id: str) -> SuiteAdmissionReport:
        raise AssertionError("refused evidence must not be replayed")

    monkeypatch.setattr(pcb, "_replay_suite_admission", no_replay)
    with pytest.raises(ValueError, match=message):
        pcb._suite_evidence(PACKAGE, imported, _suite_report(imported, **changes))
