"""Authoring and admission tool for suite-mode JavaScript/TypeScript task packages (Prompt 19,
PCB-19-4).

    python scripts/js_task_tool.py seal PACKAGE [--check]
    python scripts/js_task_tool.py validate PACKAGE
    python scripts/js_task_tool.py admit PACKAGE --report REPORT.json

``seal`` fills the computed manifest fields (file lists, output-contract digest, pinned runtime
image digest, build/test recipe digests, which are digests of the *plans* the pinned recipe
produces); ``--check`` fails instead of writing. ``validate`` runs the safe importer and the
plugin's structural validation. ``admit`` additionally executes every authored variant in the local
Docker sandbox - acceptance and static analysis in the pinned evaluator image - and writes the
machine-readable admission report.

An executable-admission pass is *not* a frozen task: ``pending_gates`` in the report names what
still has to happen (generic evaluator stage, performance baseline, judge calibration, scoring
replay, production worker tier, curator approval and owner rights confirmation), and
``quality_admission`` stays ``pending``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]
from polycodebench_core.canonical import canonical_digest, canonical_document_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, TaskOutputContract
from polycodebench_evaluation.plan_runner import PlanRunner
from polycodebench_evaluation.suite_admission import SuiteAdmission
from polycodebench_lang_javascript import JavaScriptLanguagePlugin, TypeScriptLanguagePlugin
from polycodebench_plugins_api import TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
AREAS = ("visible", "hidden", "admission")
# Install/build detritus, not task content: sealing it would ship a candidate's node_modules.
IGNORED_DIRS = frozenset({"node_modules", "build", ".cache", "out", "dist", ".vite"})

PLUGINS = {
    "javascript": JavaScriptLanguagePlugin,
    "typescript": TypeScriptLanguagePlugin,
}


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
    """Build/install directories present in the package; they must never be sealed or imported."""
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


def _plugin(manifest: dict[str, Any]) -> Any:
    language = str(manifest["task"]["primary_language"])
    factory = PLUGINS.get(language)
    if factory is None:
        raise SystemExit(f"unsupported language {language!r}; expected one of {sorted(PLUGINS)}")
    return factory()


def _candidate(task_id: str) -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "0" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


def _sealed(root: Path, plugin: Any) -> dict[str, Any]:
    manifest = _load(root)
    files = _files(root)
    manifest["visible_files"] = sorted(p for p in files if p.startswith("visible/"))
    manifest["hidden_files"] = sorted(p for p in files if not p.startswith("visible/"))
    contract = TaskOutputContract.model_validate_json(json.dumps(manifest["output_contract"]))
    manifest["task"]["output_contract_digest"] = canonical_document_digest(contract)
    runtime = manifest["runtime"]
    language = str(manifest["task"]["primary_language"])
    runtime["image_digest"] = plugin.identities.evaluator.digest
    runtime["language_plugin_id"] = language
    runtime["language_plugin_version"] = "0.1.0"
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language=language,
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "0" * 64)
    # The recipe digests are digests of the plans the pinned recipe produces, so a change to the
    # runner, the images, the lock or the harness changes them and forces a reseal.
    runtime["build_recipe_digest"] = canonical_digest(
        plugin.build_plan(view, _candidate(view.task_id)).model_dump(mode="json")
    )
    runtime["test_recipe_digest"] = canonical_digest(plugin.test_plan(view).model_dump(mode="json"))
    return manifest


def seal(root: Path, check: bool) -> int:
    plugin = _plugin(_load(root))
    stray = _stray(root)
    if stray:
        print(
            "build/install output directories must be removed before sealing: "
            + ", ".join(stray[:5]),
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
        yaml.safe_dump(sealed, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
        newline="\n",
    )
    print("manifest is sealed")
    return 0


def validate(root: Path) -> int:
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language=str(manifest["task"]["primary_language"]),
        manifest=manifest,
        files=_files(root),
    )
    report = _plugin(manifest).validate_task(draft)
    for issue in report.issues:
        print(f"{issue.severity}: {issue.code} {issue.path or ''} {issue.message}")
    print(f"package digest {package.package_digest}; plugin validation ok={report.ok}")
    return 0 if report.ok else 1


async def admit(root: Path, report_path: Path) -> int:
    plugin = _plugin(_load(root))
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    files = _files(root)
    ids = plugin.identities
    # Acceptance and static analysis both run in the evaluator image: it is the only JS/TS image
    # that ships the test runner and ESLint. The runtime and performance images deliberately carry
    # neither, so a candidate cannot inspect an analyzer from inside its own container.
    images = (ids.evaluator, ids.runtime, ids.performance)
    provider = LocalDockerSandboxProvider(
        allowed_images={record.reference: record.digest for record in images},
        state_dir=ROOT / ".cache" / "js-admission-state",
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
        # The JS/TS-specific admission question: the two languages must stay distinct identities,
        # and the image that judges a candidate must be the one that ships the analyzers.
        "pinned-recipe": (
            len({record.digest for record in images}) == len(images)
            and ids.ships("vitest", recipe="evaluator")
            and ids.ships("eslint", recipe="evaluator"),
            f"vitest={ids.ships('vitest', recipe='evaluator')}; "
            f"eslint={ids.ships('eslint', recipe='evaluator')}; "
            f"tsc={ids.ships('tsc', recipe='evaluator')}; "
            f"runtime_eslint={ids.ships('eslint', recipe='runtime')}",
        ),
    }
    runner = PlanRunner(provider, lane="admission")
    engine = SuiteAdmission(
        plugin,
        runner,
        image_digests=tuple(record.digest for record in images),
    )
    report = await engine.admit(
        files=files, manifest=manifest, package_digest=package.package_digest, precheck=precheck
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    for check in report.checks:
        print(f"{check.status.upper():4} {check.check_id} {check.detail[:100]}")
    for run in report.runs:
        print(f"     {run.name}: gates={run.gates} families={run.issue_families}")
    passed = report.executable_admission_passed
    print(f"executable admission passed={passed}; quality admission={report.quality_admission}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="js_task_tool")
    commands = parser.add_subparsers(dest="command", required=True)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("package", type=Path)
    seal_parser.add_argument("--check", action="store_true")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("package", type=Path)
    admit_parser = commands.add_parser("admit")
    admit_parser.add_argument("package", type=Path)
    admit_parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args(argv)

    if options.command == "seal":
        return seal(options.package, options.check)
    if options.command == "validate":
        return validate(options.package)
    return asyncio.run(admit(options.package, options.report))


if __name__ == "__main__":
    raise SystemExit(main())
