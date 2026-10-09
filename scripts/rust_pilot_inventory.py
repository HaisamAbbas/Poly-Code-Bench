"""Build the committed, hidden-free pilot inventory from the protected Rust task packages.

    .venv/Scripts/python.exe scripts/rust_pilot_inventory.py \\
        --protected .protected/taskpacks/rust-pilot --reports .protected/reports \\
        --output taskpacks/rust-pilot/inventory.yaml

The inventory records identities, digests, dimension/opportunity coverage and admission status.
It never copies visible text, tests, references, variants or reports' failing-case details: those
stay in protected storage. A task is listed as ``executable_admission`` only when its admission
report passed *and* names the exact package digest on disk; the pending gates remain explicit.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api.admission import SuiteAdmissionReport
from polycodebench_services.task_packages import TaskPackageImporter

TARGET_CLUSTERS = 12


ROOT = Path(__file__).resolve().parents[1]


def protected_location(package: Path) -> str:
    """Repository-relative location of a protected package (any pilot pack directory)."""
    resolved = package.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def entry(package: Path, reports: Path) -> dict[str, Any]:
    imported = TaskPackageImporter().import_package(package)
    manifest = imported.manifest
    quality = yaml.safe_load((package / "hidden" / "quality-plan.yaml").read_text(encoding="utf-8"))
    oracle = json.loads((package / "hidden" / "oracle.json").read_text(encoding="utf-8"))
    exposure = json.loads(
        (package / "admission" / "exposure-rights.json").read_text(encoding="utf-8")
    )
    report_file = reports / f"rust-{package.name}.json"
    admission: dict[str, Any] = {"status": "not_run", "report": None}
    if report_file.is_file():
        report = SuiteAdmissionReport.model_validate_json(report_file.read_text(encoding="utf-8"))
        matches = report.package_digest == imported.package_digest
        admission = {
            "status": (
                "executable_admission_passed"
                if report.executable_admission_passed and matches
                else ("stale_report" if not matches else "executable_admission_failed")
            ),
            "report_digest": report.report_digest,
            "report_package_digest": report.package_digest,
            "execution_tier": report.execution_tier,
            "checks_passed": sum(c.status == "pass" for c in report.checks),
            "checks_total": len(report.checks),
            "image_digests": list(report.image_digests),
        }
    variants = Counter(f.variant for f in manifest.fixtures)
    cases = Counter(case["case_kind"] for group in oracle["groups"] for case in group["cases"])
    return {
        "slug": package.name,
        "task_id": manifest.task.task_id,
        "version": manifest.task.version,
        "cluster_id": manifest.task.cluster_id,
        "family": manifest.task.family,
        "difficulty": manifest.task.difficulty,
        "stratum_id": manifest.task.stratum_id,
        "methodology_label": manifest.task.methodology_label,
        "source": {
            "source_kind": manifest.source.source_kind,
            "immutable_revision": manifest.source.immutable_revision,
            "rights_record_id": manifest.source.rights_record_id,
            "first_public_at": manifest.source.first_public_at,
            "date_confidence": manifest.source.date_confidence,
            "redistribution_status": manifest.rights.redistribution_status,
            "license_expression": manifest.rights.license_expression,
        },
        "split": {
            "visible_file_count": len(manifest.visible_files),
            "hidden_file_count": len(manifest.hidden_files),
            "visible_paths": sorted(manifest.visible_files),
            "dataset_split_assignment": "unassigned (curator decision at freeze)",
        },
        "applicable_dimensions": [d.value for d in manifest.quality_plan.applicable_dimensions],
        "miri": quality["miri"],
        "opportunity_tags": quality.get("opportunity_tags", []),
        "opportunities": quality.get("opportunities", {}),
        "required_analyzers": quality["required_analyzers"],
        "hidden_case_counts": dict(sorted(cases.items())),
        "variant_counts": dict(sorted(variants.items())),
        "package_digest": imported.package_digest,
        "manifest_digest": imported.manifest_digest,
        "visible_bundle_digest": imported.visible_digest,
        "hidden_bundle_digest": imported.hidden_digest,
        "protected_location": protected_location(package),
        "exposure": {
            "first_public_at": exposure["first_public_at"],
            "public_exposure_review": exposure["public_exposure_review"],
            "provider_transmission_policy": exposure["provider_transmission_policy"],
            "rights_status": exposure["rights"]["status"],
            "license_expression": exposure["rights"]["license_expression"],
            "owner_confirmation": exposure["rights"]["owner_confirmation"],
        },
        "admission": admission,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rust_pilot_inventory")
    parser.add_argument("--protected", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    packages = sorted(p for p in args.protected.iterdir() if (p / "manifest.yaml").is_file())
    tasks = [entry(p, args.reports) for p in packages]
    dims = Counter(d for t in tasks for d in t["applicable_dimensions"])
    passed = [t for t in tasks if t["admission"]["status"] == "executable_admission_passed"]
    document = {
        "schema_version": 1,
        "kind": "rust_pilot_inventory",
        "note": (
            "Pilot inventory of authored Rust clusters. Hidden bundles, references, variants and "
            "tests live in protected storage (git-ignored); only identities and digests are here. "
            "No task is fully admitted or frozen: quality admission awaits Prompts 12-15 and the "
            "curator/owner approvals listed in pending_gates."
        ),
        "target_independent_clusters": TARGET_CLUSTERS,
        "authored_clusters": len({t["cluster_id"] for t in tasks}),
        "executable_admission_passed": len(passed),
        "fully_admitted": 0,
        "frozen": 0,
        "dimension_coverage": dict(sorted(dims.items())),
        "miri_required_tasks": sum(t["miri"] == "required" for t in tasks),
        "miri_optional_tasks": sum(t["miri"] == "optional" for t in tasks),
        "pending_gates": [
            "generic-evaluator-stage-integration (Prompt 12)",
            "performance-baseline-canary-and-paired-measurement (Prompt 13)",
            "judge-anchors-and-human-calibration (Prompt 14)",
            "deterministic-scoring-replay-and-golden-calculations (Prompt 15)",
            "production-worker-execution-tier (Prompt 06 owner-deferred)",
            "curator-approval-and-task-freeze",
            "owner-rights-confirmation",
            "hidden-lane-object-store-registration-at-freeze",
        ],
        "tasks": tasks,
    }
    document["inventory_digest"] = canonical_digest(json.loads(json.dumps(document)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )
    print(
        f"{len(tasks)} packages; {len(passed)} executable-admission-passed; "
        f"clusters {document['authored_clusters']}/{TARGET_CLUSTERS}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
