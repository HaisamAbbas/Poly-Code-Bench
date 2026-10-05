"""Authoring and admission tool for suite-mode Rust task packages.

    python scripts/rust_task_tool.py seal PACKAGE [--check]
    python scripts/rust_task_tool.py validate PACKAGE
    python scripts/rust_task_tool.py admit PACKAGE --report REPORT.json

``seal`` fills the computed manifest fields (file lists, output-contract digest, pinned runtime
image digest, recipe digests); ``--check`` fails instead of writing. ``validate`` runs the safe
importer and the plugin's structural validation. ``admit`` additionally executes every authored
variant in the local Docker sandbox and writes the machine-readable admission report. Exit status
is 0 only when everything the command verified passed; an executable-admission pass is *not* a
frozen task (see ``pending_gates`` in the report).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from polycodebench_core.canonical import canonical_digest, canonical_document_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, TaskOutputContract
from polycodebench_evaluation.plan_runner import PlanRunner
from polycodebench_evaluation.suite_admission import SuiteAdmission
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import TaskDraft
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
AREAS = ("visible", "hidden", "admission")
# Tool caches are build detritus, not task content: sealing one into a package would ship the
# reference solution's analysis state to every candidate and break the output contract.
IGNORED_DIRS = frozenset({"target", ".cache"})


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for area in AREAS:
        base = root / area
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not IGNORED_DIRS.intersection(path.parts):
                    found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _stray(root: Path) -> list[str]:
    """Tool-cache directories present in the package; they must never be sealed or imported."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and path.name in IGNORED_DIRS
    )


def _load(root: Path) -> dict[str, Any]:
    document = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit("manifest.yaml must be a mapping")
    return document


def _sealed(root: Path, plugin: RustLanguagePlugin) -> dict[str, Any]:
    manifest = _load(root)
    files = _files(root)
    manifest["visible_files"] = sorted(p for p in files if p.startswith("visible/"))
    manifest["hidden_files"] = sorted(p for p in files if not p.startswith("visible/"))
    contract = TaskOutputContract.model_validate_json(json.dumps(manifest["output_contract"]))
    manifest["task"]["output_contract_digest"] = canonical_document_digest(contract)
    runtime = manifest["runtime"]
    runtime["image_digest"] = plugin.identities.runtime.digest
    runtime["language_plugin_id"] = "rust"
    runtime["language_plugin_version"] = "0.1.0"
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="rust",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "0" * 64)
    candidate = Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": view.task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "0" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )
    runtime["build_recipe_digest"] = canonical_digest(
        plugin.build_plan(view, candidate).model_dump(mode="json")
    )
    runtime["test_recipe_digest"] = canonical_digest(plugin.test_plan(view).model_dump(mode="json"))
    return manifest


def seal(root: Path, check: bool) -> int:
    plugin = RustLanguagePlugin()
    stray = _stray(root)
    if stray:
        print(
            "tool cache directories must be removed before sealing: " + ", ".join(stray[:5]),
            file=sys.stderr,
        )
        return 1
    current = _load(root)
    sealed = _sealed(root, plugin)
    if canonical_digest(current) == canonical_digest(sealed):
        print("manifest is sealed")
        return 0
    if check:
        print("manifest is stale: run `seal` to refresh computed fields", file=sys.stderr)
        return 1
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(sealed, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )
    print("manifest sealed")
    return 0


def validate(root: Path) -> int:
    plugin = RustLanguagePlugin()
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    files = _files(root)
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="rust",
        manifest=manifest,
        files=files,
    )
    report = plugin.validate_task(draft)
    for issue in report.issues:
        print(f"{issue.severity}: {issue.code} {issue.path or ''} {issue.message}")
    print(f"package digest {package.package_digest}; plugin validation ok={report.ok}")
    return 0 if report.ok else 1


async def admit(root: Path, report_path: Path) -> int:
    plugin = RustLanguagePlugin()
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    files = _files(root)
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "rust-admission-state",
        operation_timeout_seconds=120,
    )
    rights = manifest["rights"]
    precheck = {
        "strict-schema": (True, "manifest validated by the safe importer"),
        "safe-paths-and-snapshot": (True, "contained regular files, no links, no git metadata"),
        "visible-hidden-separation": (True, "physically separate archives; disclosure scan ran"),
        "disclosure-scan": (True, "no hidden bytes/paths/digests or credentials in visible bundle"),
        "rights-and-provenance": (
            rights["redistribution_status"] in {"cleared", "authored_fixture"}
            and bool(manifest["source"]["immutable_revision"]),
            f"status={rights['redistribution_status']}; license={rights['license_expression']}",
        ),
    }
    # ``RustProfile.evaluate`` returns the Rust plugin's own ``ProfileResult`` model, whose fields
    # are a superset of the shared ``plugins-api`` one, so the plugin really is an
    # ``ExecutableLanguagePlugin``; only the nominal return type differs. Verified against the
    # protocol's resolve/owner/evaluate by importing the plugin.
    engine = SuiteAdmission(
        cast("ExecutableLanguagePlugin", plugin),
        PlanRunner(provider, lane="admission"),
        image_digests=(ids.runtime.digest, ids.evaluator.digest),
    )
    report = await engine.admit(
        files=files, manifest=manifest, package_digest=package.package_digest, precheck=precheck
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    for check in report.checks:
        print(f"{check.status.upper():4} {check.check_id} {check.detail[:100]}")
    print(
        f"executable admission passed={report.executable_admission_passed}; "
        f"quality admission={report.quality_admission}; report {report.report_digest}"
    )
    return 0 if report.executable_admission_passed else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rust_task_tool")
    parser.add_argument(
        "--protected",
        type=Path,
        default=ROOT / ".protected/taskpacks/rust-pilot",
        help="package root used by the batch --seal-all mode",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "validate", "admit"):
        item = sub.add_parser(name)
        item.add_argument("package", type=Path)
        if name == "seal":
            item.add_argument("--check", action="store_true")
        if name == "admit":
            item.add_argument("--report", type=Path, required=True)
    seal_all = sub.add_parser("seal-all", help="reseal every pilot package against current images")
    seal_all.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "seal-all":
        failures = 0
        for package in sorted(
            p for p in args.protected.iterdir() if (p / "manifest.yaml").is_file()
        ):
            code = seal(package, args.check)
            print(f"{package.name}: {'ok' if code == 0 else 'FAILED'}")
            failures += code
        return failures
    root = args.package.resolve()
    if args.command == "seal":
        return seal(root, args.check)
    if args.command == "validate":
        return validate(root)
    return asyncio.run(admit(root, args.report))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
