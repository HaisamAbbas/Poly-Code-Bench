"""Seal, validate and execute the authored Java task fixture through shared admission.

    uv run python scripts/java_task_tool.py seal PACKAGE [--check]
    uv run python scripts/java_task_tool.py validate PACKAGE
    uv run python scripts/java_task_tool.py admit PACKAGE --report REPORT.json

This is a local conformance fixture, not a model benchmark task. `admit` uses the same safe task
package importer, PlanRunner, typed evidence parsers and SuiteAdmission service as the other
language extensions; its report keeps the production-only gates pending.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from polycodebench_core.canonical import canonical_digest, canonical_document_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, TaskOutputContract
from polycodebench_evaluation.plan_runner import PlanRunner
from polycodebench_evaluation.suite_admission import SuiteAdmission
from polycodebench_lang_java import JavaLanguagePlugin
from polycodebench_plugins_api import TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = ROOT / "plugins/languages/java/fixtures/top-words"
AREAS = ("visible", "hidden", "admission")
IGNORED_DIRS = frozenset({"target", ".cache", "__pycache__"})


def files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for area in AREAS:
        base = root / area
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not IGNORED_DIRS.intersection(path.parts):
                    found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def load(root: Path) -> dict[str, Any]:
    document = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit("manifest.yaml must be a mapping")
    return document


def sealed(root: Path, plugin: JavaLanguagePlugin) -> dict[str, Any]:
    manifest = load(root)
    package_files = files(root)
    manifest["visible_files"] = sorted(p for p in package_files if p.startswith("visible/"))
    manifest["hidden_files"] = sorted(p for p in package_files if not p.startswith("visible/"))
    contract = TaskOutputContract.model_validate_json(json.dumps(manifest["output_contract"]))
    manifest["task"]["output_contract_digest"] = canonical_document_digest(contract)
    runtime = manifest["runtime"]
    runtime["image_digest"] = plugin.identities.runtime.digest
    runtime["language_plugin_id"] = "java"
    runtime["language_plugin_version"] = "0.1.0"
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="java",
        manifest=manifest,
        files=package_files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "0" * 64, int(manifest["task"]["version"]))
    candidate = Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": view.task_id,
                "task_version": view.task_version,
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
    plugin = JavaLanguagePlugin()
    current = load(root)
    updated = sealed(root, plugin)
    if canonical_digest(current) == canonical_digest(updated):
        print("manifest is sealed")
        return 0
    if check:
        print("manifest is stale: run `seal` to refresh computed fields", file=sys.stderr)
        return 1
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(updated, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )
    print("manifest sealed against current Java recipe identities")
    return 0


def validate(root: Path) -> int:
    plugin = JavaLanguagePlugin()
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = load(root)
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="java",
        manifest=manifest,
        files=files(root),
    )
    report = plugin.validate_task(draft)
    for issue in report.issues:
        print(f"{issue.severity}: {issue.code} {issue.path or ''} {issue.message}")
    print(f"package digest {package.package_digest}; plugin validation ok={report.ok}")
    return 0 if report.ok else 1


async def admit(root: Path, report_path: Path) -> int:
    plugin = JavaLanguagePlugin()
    if validate(root) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = load(root)
    package_files = files(root)
    identities = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            identities.runtime.reference: identities.runtime.digest,
            identities.evaluator.reference: identities.evaluator.digest,
        },
        state_dir=ROOT / ".cache/java-admission-state",
        operation_timeout_seconds=120,
    )
    rights = manifest["rights"]
    precheck = {
        "strict-schema": (True, "safe task package importer accepted the fixture manifest"),
        "safe-paths-and-snapshot": (True, "contained regular files; no links or undeclared files"),
        "visible-hidden-separation": (True, "separate archives; disclosure scan passed"),
        "disclosure-scan": (True, "visible bundle contains no hidden bytes or hidden digests"),
        "rights-and-provenance": (
            rights["redistribution_status"] == "authored_fixture"
            and bool(manifest["source"]["immutable_revision"]),
            "local authored fixture; no provider transmission or benchmark result is claimed",
        ),
    }
    engine = SuiteAdmission(
        plugin,
        PlanRunner(provider, lane="admission"),
        profile_id="java-fixture-admission-v1",
        image_digests=(identities.runtime.digest, identities.evaluator.digest),
    )
    report = await engine.admit(
        files=package_files,
        manifest=manifest,
        package_digest=package.package_digest,
        precheck=precheck,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    for check in report.checks:
        print(f"{check.status.upper():4} {check.check_id} {check.detail[:120]}")
    print(
        f"executable admission passed={report.executable_admission_passed}; "
        f"quality admission={report.quality_admission}; report {report.report_digest}"
    )
    return 0 if report.executable_admission_passed else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="java_task_tool")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "validate", "admit"):
        item = sub.add_parser(name)
        item.add_argument("package", type=Path)
        if name == "seal":
            item.add_argument("--check", action="store_true")
        if name == "admit":
            item.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.package.resolve()
    if args.command == "seal":
        return seal(root, args.check)
    if args.command == "validate":
        return validate(root)
    return asyncio.run(admit(root, args.report))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
