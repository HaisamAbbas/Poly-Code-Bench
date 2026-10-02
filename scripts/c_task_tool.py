"""Authoring and admission tool for suite-mode C task packages (Prompt 20, PCB-20-4).

    python scripts/c_task_tool.py seal PACKAGE [--check]
    python scripts/c_task_tool.py validate PACKAGE
    python scripts/c_task_tool.py admit PACKAGE --report REPORT.json

``seal`` fills the computed manifest fields (file lists, output-contract digest, pinned runtime
image digest, build/test recipe digests, which are digests of the *plans* the pinned recipe
produces); ``--check`` fails instead of writing. ``validate`` runs the safe importer and the
plugin's structural validation. ``admit`` additionally executes every authored variant in the
local Docker sandbox - acceptance in the release runtime image, static analysis in the evaluator
image, ASan/UBSan/Valgrind in the instrumented image, the workload smoke in the performance image -
and writes the machine-readable admission report. Exit status is 0 only when everything the
command verified passed; an executable-admission pass is *not* a frozen task (see
``pending_gates`` in the report).
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
from polycodebench_lang_c import CLanguagePlugin
from polycodebench_plugins_api import TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
AREAS = ("visible", "hidden", "admission")
# Build output is detritus, not task content: sealing it would ship a reference binary.
IGNORED_DIRS = frozenset({"build", ".cache", "out"})


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
    """Build-output directories present in the package; they must never be sealed or imported."""
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


def _sealed(root: Path, plugin: CLanguagePlugin) -> dict[str, Any]:
    manifest = _load(root)
    files = _files(root)
    manifest["visible_files"] = sorted(p for p in files if p.startswith("visible/"))
    manifest["hidden_files"] = sorted(p for p in files if not p.startswith("visible/"))
    contract = TaskOutputContract.model_validate_json(json.dumps(manifest["output_contract"]))
    manifest["task"]["output_contract_digest"] = canonical_document_digest(contract)
    runtime = manifest["runtime"]
    # The acceptance lane's image is the *release* runtime, never the instrumented one: a sanitizer
    # can never decide whether a candidate passes.
    runtime["image_digest"] = plugin.identities.runtime.digest
    runtime["language_plugin_id"] = "c"
    runtime["language_plugin_version"] = "0.1.0"
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="c",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "0" * 64)
    # The recipe digests are digests of the plans the pinned recipe produces, so a change to the
    # flags, the images or the harness changes them and forces a reseal.
    runtime["build_recipe_digest"] = canonical_digest(
        plugin.build_plan(view, _candidate(view.task_id)).model_dump(mode="json")
    )
    runtime["test_recipe_digest"] = canonical_digest(plugin.test_plan(view).model_dump(mode="json"))
    return manifest


def seal(root: Path, check: bool) -> int:
    plugin = CLanguagePlugin()
    stray = _stray(root)
    if stray:
        print(
            "build output directories must be removed before sealing: " + ", ".join(stray[:5]),
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
    print("manifest sealed")
    return 0


def validate(root: Path) -> int:
    plugin = CLanguagePlugin()
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="c",
        manifest=manifest,
        files=_files(root),
    )
    report = plugin.validate_task(draft)
    for issue in report.issues:
        print(f"{issue.severity}: {issue.code} {issue.path or ''} {issue.message}")
    print(f"package digest {package.package_digest}; plugin validation ok={report.ok}")
    return 0 if report.ok else 1


async def _baseline_warning_debt(
    plugin: CLanguagePlugin, runner: PlanRunner, manifest: dict[str, Any], files: dict[str, bytes]
) -> tuple[bool, str]:
    """Compile the frozen baseline with the task's own build plan and compare its warning count.

    ``warning_policy.baseline_warnings`` decides what a candidate's warnings are measured against, so
    an estimated number would silently move every candidate's debt. It is measured here, in the
    release image, with exactly the flags a candidate is built with.
    """
    from polycodebench_evaluation.plan_runner import materialize_inputs

    draft = TaskDraft(
        task_id=manifest["task"]["task_id"], primary_language="c", manifest=manifest, files=files
    )
    view = plugin.freeze_view(draft, "sha256:" + "0" * 64)
    policy = view.quality["warning_policy"]
    allowed = manifest["output_contract"]["allowed_paths"]
    candidate = {path: files[f"visible/repo/{path}"] for path in allowed}
    config = dict(plugin.trusted_inputs(files, view))
    plan = plugin.build_plan(view, _candidate(view.task_id))
    run = await runner.run(
        plan,
        materialize_inputs(plan, {"candidate": candidate, "overlay": {}, "config": config}),
        stage_id="c-baseline-debt",
    )
    document_path = next(o.path for o in plan.outputs if o.path.endswith(".build.json"))
    document = json.loads(run.outputs[document_path].decode("utf-8"))
    measured = int(document["warning_count"])
    declared = int(policy["baseline_warnings"])
    clean_ok = bool(policy["baseline_warning_clean"]) == (measured == 0)
    detail = (
        f"baseline compiled={document['linked']} errors={document['error_count']} "
        f"warnings measured={measured} declared={declared} "
        f"clean_declared={policy['baseline_warning_clean']} mode={policy['mode']} "
        f"set={policy['warning_set']}"
    )
    return measured == declared and clean_ok and document["error_count"] == 0, detail


async def admit(root: Path, report_path: Path) -> int:
    plugin = CLanguagePlugin()
    if seal(root, check=True) != 0:
        return 1
    package = TaskPackageImporter().import_package(root)
    manifest = _load(root)
    files = _files(root)
    ids = plugin.identities
    images = (ids.runtime, ids.evaluator, ids.instrumented, ids.performance)
    provider = LocalDockerSandboxProvider(
        # All four recipes: acceptance (runtime), static analysis (evaluator), the sanitizer and
        # Valgrind lanes (instrumented) and the workload smoke (performance). Leaving one out would
        # make its lane unrunnable, which admission would then report as `missing`, not as clean.
        allowed_images={record.reference: record.digest for record in images},
        state_dir=ROOT / ".cache" / "c-admission-state",
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
        # The C-specific admission question: are the four recipes the pinned, distinct images, and
        # is the measurement image the uninstrumented one?
        "pinned-recipe": (
            len({record.digest for record in images}) == len(images)
            and ids.performance.instrumentation == "none"
            and ids.runtime.instrumentation == "none"
            and ids.instrumented.instrumentation != "none",
            f"cc={ids.runtime.cc}; std={ids.runtime.c_standard}; "
            f"instrumented={ids.instrumented.instrumentation}; "
            f"performance={ids.performance.instrumentation}/{ids.performance.optimization}",
        ),
    }
    runner = PlanRunner(provider, lane="admission")
    # PCB-20-3: the declared baseline warning debt is a measured fact, not an annotation.
    precheck["baseline-warning-debt"] = await _baseline_warning_debt(plugin, runner, manifest, files)
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
        print(
            f"     {run.name}: gates={list(run.gates)} failing={list(run.failing_cases)[:4]} "
            f"families={list(run.issue_families)[:6]} scans={list(run.analyzer_scans)}"
        )
    print(
        f"executable admission passed={report.executable_admission_passed}; "
        f"quality admission={report.quality_admission}; report {report.report_digest}"
    )
    return 0 if report.executable_admission_passed else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="c_task_tool")
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
