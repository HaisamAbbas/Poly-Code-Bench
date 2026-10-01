"""PCB-11-4: the committed Rust pilot inventory is complete, honest and hidden-free.

The inventory is generated from protected packages and admission reports by
``scripts/rust_pilot_inventory.py``. These tests read only the committed YAML (and, when the
git-ignored protected storage is present, check that every recorded digest still matches the
package on disk).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from polycodebench_core.canonical import canonical_digest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "taskpacks" / "rust-pilot" / "inventory.yaml"
PROTECTED = ROOT / ".protected" / "taskpacks" / "rust-pilot"
document = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
tasks = document["tasks"]


def test_twelve_independent_clusters_each_executably_admitted_and_none_frozen() -> None:
    assert document["target_independent_clusters"] == 12
    assert document["authored_clusters"] == 12 == len({t["cluster_id"] for t in tasks})
    assert document["executable_admission_passed"] == 12
    assert {t["admission"]["status"] for t in tasks} == {"executable_admission_passed"}
    # Executable admission is not quality admission: nothing is claimed fully admitted or frozen.
    assert document["fully_admitted"] == 0 and document["frozen"] == 0
    gates = " ".join(document["pending_gates"])
    for prompt in ("Prompt 12", "Prompt 13", "Prompt 14", "Prompt 15"):
        assert prompt in gates
    assert "curator-approval-and-task-freeze" in document["pending_gates"]
    assert "owner-rights-confirmation" in document["pending_gates"]


def test_every_entry_carries_source_split_and_applicability_metadata() -> None:
    for task in tasks:
        assert task["source"]["immutable_revision"] and task["source"]["license_expression"]
        assert task["source"]["redistribution_status"] == "authored_fixture"
        assert task["split"]["visible_file_count"] == len(task["split"]["visible_paths"]) > 0
        assert task["split"]["hidden_file_count"] > 0
        assert all(p.startswith("visible/") for p in task["split"]["visible_paths"])
        assert task["applicable_dimensions"] and task["opportunities"]
        assert (
            task["required_analyzers"] == ["clippy", "context"]
            or "miri" in task["required_analyzers"]
        )
        assert task["miri"] in {"required", "optional", "unsupported"}
        assert task["exposure"]["owner_confirmation"] == "pending"
        assert task["admission"]["execution_tier"] == "development_sandbox"
        assert task["admission"]["checks_passed"] == task["admission"]["checks_total"] >= 21


def test_variant_coverage_matches_the_conformance_categories() -> None:
    for task in tasks:
        counts = task["variant_counts"]
        assert counts["reference"] == 1
        assert counts["faulty"] >= 1 and counts["alternative"] >= 1
        assert counts["quality_defective"] == 1 and counts["timeout"] >= 1
    assert sum(t["miri"] == "required" for t in tasks) >= 1  # at least one unsafe/Miri cluster
    assert any("security" in t["applicable_dimensions"] for t in tasks)
    assert any("concurrency" in t["opportunity_tags"] for t in tasks)


def test_the_inventory_is_hidden_free_and_self_consistent() -> None:
    text = INVENTORY.read_text(encoding="utf-8")
    for needle in ("fn ", "#[test]", "unsafe {", "failing_cases", "hidden/", "admission/"):
        assert needle not in text, needle
    recorded = dict(document)
    digest = recorded.pop("inventory_digest")
    assert canonical_digest(recorded) == digest


@pytest.mark.skipif(not PROTECTED.is_dir(), reason="protected task storage is not present")
def test_recorded_digests_still_match_the_protected_packages() -> None:
    from polycodebench_services.task_packages import TaskPackageImporter

    for task in tasks:
        imported = TaskPackageImporter().import_package(PROTECTED / task["slug"])
        assert imported.package_digest == task["package_digest"], task["slug"]
        assert task["admission"]["report_package_digest"] == task["package_digest"]
