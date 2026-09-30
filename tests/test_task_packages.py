"""Task package, privacy, cutoff, and admission-report contract checks."""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import (
    AdmissionExecutionReport,
    ModelCutoffProvenance,
    TaskSet,
    TaskSetMember,
)
from polycodebench_core.tasksets import validate_cluster_split_assignments
from polycodebench_services.task_packages import (
    TaskPackageImporter,
    post_cutoff_eligibility,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "taskpacks" / "admission-smoke"
DIGEST = "sha256:" + "a" * 64


def _copy_package(tmp_path: Path) -> Path:
    result = tmp_path / "package"
    shutil.copytree(PACKAGE, result)
    return result


@contextmanager
def _package_tmp() -> Iterator[Path]:
    path = ROOT / ".cache" / f"p05-task-test-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_import_builds_deterministic_visible_and_hidden_archives() -> None:
    importer = TaskPackageImporter()
    first = importer.import_package(PACKAGE)
    second = importer.import_package(PACKAGE)
    assert first.visible_archive == second.visible_archive
    assert first.hidden_archive == second.hidden_archive
    assert first.visible_digest == second.visible_digest
    with ZipFile(io.BytesIO(first.visible_archive)) as visible:
        names = visible.namelist()
        assert "visible-manifest.json" in names
        assert all("admission/" not in name and "hidden/" not in name for name in names)
        contents = b"".join(visible.read(name) for name in names)
        assert b"oracle.expected" not in contents
        assert b"42\n" not in contents
    with ZipFile(io.BytesIO(first.hidden_archive)) as hidden:
        assert "hidden/oracle.expected" in hidden.namelist()
        assert "admission/reference.py" in hidden.namelist()


def test_import_rejects_duplicate_manifest_keys_and_unsafe_paths() -> None:
    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path)
        manifest = package / "manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "schema_version: 1\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="invalid"):
            TaskPackageImporter().import_package(package)

    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path / "unsafe")
        manifest = package / "manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "- hidden/oracle.expected", "- hidden/../visible/task.md"
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            TaskPackageImporter().import_package(package)


def test_import_rejects_hidden_byte_leak_into_visible_bundle() -> None:
    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path)
        secret_bytes = (package / "hidden" / "oracle.expected").read_bytes()
        (package / "visible" / "copied-oracle.txt").write_bytes(secret_bytes)
        manifest_path = package / "manifest.yaml"
        text = manifest_path.read_text(encoding="utf-8")
        text = text.replace(
            "  - visible/repo/README.md\nhidden_files:",
            "  - visible/repo/README.md\n  - visible/copied-oracle.txt\nhidden_files:",
        )
        manifest_path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="hidden file bytes"):
            TaskPackageImporter().import_package(package)


def test_cutoff_uses_verified_exposure_not_curated_date() -> None:
    after = post_cutoff_eligibility(
        earliest_public_at="2024-02-01T00:00:00Z",
        date_confidence="verified",
        cutoff_at="2024-01-01T00:00:00Z",
        cutoff_confidence="provider_declared",
    )
    assert after == (True, "earliest_public_exposure_after_declared_cutoff")
    before = post_cutoff_eligibility(
        earliest_public_at="2023-01-01T00:00:00Z",
        date_confidence="verified",
        cutoff_at="2024-01-01T00:00:00Z",
        cutoff_confidence="provider_declared",
    )
    assert before == (False, "earliest_public_exposure_not_after_cutoff")
    unknown = post_cutoff_eligibility(
        earliest_public_at=None,
        date_confidence="unknown",
        cutoff_at=None,
        cutoff_confidence="unknown",
    )
    assert unknown == (False, "model_cutoff_unknown")


def test_related_variants_cannot_cross_designated_splits() -> None:
    validate_cluster_split_assignments(
        [("repo-issue-cluster", "public_development"), ("repo-issue-cluster", "public_development")]
    )
    with pytest.raises(ValueError, match="different splits"):
        validate_cluster_split_assignments(
            [
                ("repo-issue-cluster", "public_development"),
                ("repo-issue-cluster", "private_heldout"),
            ]
        )


def test_task_set_cutoff_and_membership_contracts_reject_unknowns() -> None:
    cutoff = ModelCutoffProvenance.model_validate(
        {
            "schema_version": 1,
            "kind": "model_cutoff_provenance",
            "model_revision_digest": DIGEST,
            "cutoff_at": None,
            "provenance_uri": None,
            "confidence": "unknown",
            "provider_prior_access": "possible",
        }
    )
    member = TaskSetMember.model_validate(
        {
            "schema_version": 1,
            "kind": "task_set_member",
            "task_digest": DIGEST,
            "cluster_id": "cluster-a",
            "stratum_id": "python-small",
            "sampling_weight_bp": 10_000,
            "earliest_public_at": None,
            "exposure_confidence": "unknown",
        }
    )
    document = TaskSet.model_validate(
        {
            "schema_version": 1,
            "kind": "task_set",
            "task_set_id": "development-v1",
            "version": 1,
            "split": "fixture",
            "split_seed": "18446744073709551615",
            "scoring_policy_digest": DIGEST,
            "method_deviation_ids": [],
            "members": [member.model_dump(mode="json")],
            "model_cutoffs": [cutoff.model_dump(mode="json")],
        }
    )
    assert document.model_cutoffs[0].confidence == "unknown"
    duplicate = document.model_dump(mode="json")
    duplicate["members"].append(duplicate["members"][0])
    with pytest.raises(ValueError, match="unique"):
        TaskSet.model_validate(duplicate)


def test_admission_report_digest_is_bound_to_observed_fixture_results() -> None:
    body = json.loads(
        (
            ROOT / "docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json"
        ).read_text(encoding="utf-8")
    )
    parsed = AdmissionExecutionReport.model_validate(body)
    assert parsed.passed
    body["passed"] = False
    with pytest.raises(ValueError, match="digest"):
        AdmissionExecutionReport.model_validate(body)


@pytest.mark.parametrize(
    "mutation", ["empty", "missing_variant", "one_reference_run", "wrong_reference"]
)
def test_admission_summary_cannot_override_observed_execution_evidence(mutation: str) -> None:
    body = json.loads(
        (
            ROOT / "docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json"
        ).read_text(encoding="utf-8")
    )
    if mutation == "empty":
        body["executions"] = []
    elif mutation == "missing_variant":
        body["executions"] = [
            item for item in body["executions"] if item["variant"] != "alternative"
        ]
    elif mutation == "one_reference_run":
        reference = body["executions"][0]
        reference["repetitions"] = 1
        for field in (
            "exit_codes",
            "matched_expected",
            "timed_out",
            "stdout_digests",
            "stderr_digests",
        ):
            reference[field] = reference[field][:1]
    else:
        body["executions"][0]["matched_expected"][0] = False
    body["report_digest"] = canonical_digest(
        {k: v for k, v in body.items() if k != "report_digest"}
    )
    with pytest.raises(ValueError):
        AdmissionExecutionReport.model_validate(body)


@pytest.mark.parametrize("area", ["visible", "hidden", "admission"])
def test_undeclared_snapshot_files_are_rejected_in_every_directory(area: str) -> None:
    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path)
        (package / area / "undeclared.txt").write_bytes(b"undeclared asset")
        with pytest.raises(ValueError, match="undeclared"):
            TaskPackageImporter().import_package(package)


@pytest.mark.parametrize(
    "path", ["admission/reference.py", "visible/task.md", "hidden/oracle.expected"]
)
def test_any_changed_package_bytes_invalidate_admission(path: str) -> None:
    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path)
        importer = TaskPackageImporter()
        before = importer.import_package(package)
        (package / path).write_bytes((package / path).read_bytes() + b"changed\n")
        after = importer.import_package(package)
        assert before.manifest_digest == after.manifest_digest
        assert before.package_digest != after.package_digest


def test_fixture_reference_cannot_be_moved_into_the_visible_bundle() -> None:
    with _package_tmp() as tmp_path:
        package = _copy_package(tmp_path)
        reference = package / "admission/reference.py"
        (package / "visible/reference.py").write_bytes(reference.read_bytes())
        reference.unlink()
        manifest = package / "manifest.yaml"
        body = manifest.read_text(encoding="utf-8")
        body = body.replace("  - admission/reference.py\n", "")
        body = body.replace(
            "  - visible/repo/README.md\n", "  - visible/repo/README.md\n  - visible/reference.py\n"
        )
        body = body.replace(
            "solution_path: admission/reference.py", "solution_path: visible/reference.py"
        )
        manifest.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="solutions must remain"):
            TaskPackageImporter().import_package(package)
