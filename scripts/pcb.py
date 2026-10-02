"""Small local CLI slice for task package import, admission and task-set freeze."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

from polycodebench_core.canonical import canonical_digest, canonical_json_bytes, parse_json_strict
from polycodebench_core.models import (
    AdmissionExecutionReport,
    AdmissionReport,
    TaskSet,
    TaskVersion,
)
from polycodebench_persistence.database import Database
from polycodebench_persistence.identities import PostgresIdentityRepository
from polycodebench_persistence.tasks import PostgresTaskRepository
from polycodebench_services.rbac import Principal, Role
from polycodebench_services.task_fixture_runner import (
    FixtureAdmissionResult,
    validate_authored_fixtures,
)
from polycodebench_services.task_packages import TaskPackageImporter
from polycodebench_services.tasks import TaskAdmissionService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb")
    parser.add_argument("--request-id", default=None)
    commands = parser.add_subparsers(dest="domain", required=True)
    task = commands.add_parser("task")
    task_commands = task.add_subparsers(dest="operation", required=True)
    task_import = task_commands.add_parser("import")
    task_import.add_argument("package", type=Path)
    task_import.add_argument("--output", type=Path, default=Path(".cache/task-imports"))
    task_validate = task_commands.add_parser("validate")
    task_validate.add_argument("package", type=Path)
    task_validate.add_argument("--admission-profile", default="admission-v1")
    task_validate.add_argument("--report", required=True, type=Path)
    task_freeze = task_commands.add_parser("freeze")
    task_freeze.add_argument("--package", required=True, type=Path)
    task_freeze.add_argument("--report", required=True, type=Path)
    task_freeze.add_argument("--manifest-artifact-id", required=True)
    task_freeze.add_argument("--visible-artifact-id", required=True)
    task_freeze.add_argument("--hidden-artifact-id", required=True)

    taskset = commands.add_parser("taskset")
    taskset_commands = taskset.add_subparsers(dest="operation", required=True)
    create = taskset_commands.add_parser("create")
    create.add_argument("--document", required=True, type=Path)
    create.add_argument("--manifest-artifact-id", required=True)
    freeze = taskset_commands.add_parser("freeze")
    freeze.add_argument("--task-set-id", required=True)
    freeze.add_argument("--digest", required=True)
    return parser


def _load_json(path: Path) -> dict[str, object]:
    value = parse_json_strict(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _report(package_digest: str, result: FixtureAdmissionResult) -> dict[str, object]:
    executions = [
        {
            "schema_version": 1,
            "kind": "fixture_execution_evidence",
            "name": item.name,
            "variant": item.variant,
            "repetitions": item.repetitions,
            "exit_codes": list(item.exit_codes),
            "matched_expected": list(item.matched_expected),
            "timed_out": list(item.timed_out),
            "stdout_digests": list(item.stdout_digests),
            "stderr_digests": list(item.stderr_digests),
        }
        for item in result.executions
    ]
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": "admission_execution_report",
        "profile_id": result.profile_id,
        "execution_tier": result.execution_tier,
        "runtime_image_digest": result.runtime_image_digest,
        "isolation_profile": "docker-no-network-readonly-nonroot-v1",
        "package_digest": package_digest,
        "checks": {
            "schema_version": 1,
            "kind": "admission_checks",
            "reference": result.reference_check,
            "faulty": result.faulty_check,
            "alternative": result.alternative_check,
            "flakiness": result.flakiness_check,
            "rights": result.rights_check,
            "disclosure": result.disclosure_check,
        },
        "executions": executions,
        "passed": result.passed,
    }
    document["report_digest"] = canonical_digest(document)
    return AdmissionExecutionReport.model_validate(document).model_dump(mode="json")


def _write_new(path: Path, data: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("refusing to overwrite existing task artifact or admission evidence")
    with target.open("xb") as output:
        output.write(data)


def _local_artifact_import(args: argparse.Namespace) -> int:
    imported = TaskPackageImporter().import_package(args.package)
    target = args.output / f"{imported.manifest.task.task_id}-v{imported.manifest.task.version}"
    target.mkdir(parents=True, exist_ok=False)
    _write_new(target / "visible.bundle.zip", imported.visible_archive)
    _write_new(target / "hidden.bundle.zip", imported.hidden_archive)
    summary = {
        "schema_version": 1,
        "kind": "task_import_result",
        "task_id": imported.manifest.task.task_id,
        "version": imported.manifest.task.version,
        "package_digest": imported.package_digest,
        "manifest_digest": imported.manifest_digest,
        "visible_digest": imported.visible_digest,
        "hidden_digest": imported.hidden_digest,
        "visible_archive": "visible.bundle.zip",
        "hidden_archive": "hidden.bundle.zip",
        "execution_tier": "not_run",
    }
    _write_new(target / "import-result.json", canonical_json_bytes(summary))
    print(json.dumps(summary, indent=2))
    return 0


def _task_validate(args: argparse.Namespace) -> int:
    if args.admission_profile != "admission-v1":
        raise ValueError("unsupported admission profile")
    imported = TaskPackageImporter().import_package(args.package)
    result = validate_authored_fixtures(imported)
    report = _report(imported.package_digest, result)
    _write_new(args.report, canonical_json_bytes(report))
    print(json.dumps(report, indent=2))
    return 0 if result.passed else 2


def _service() -> tuple[Database, TaskAdmissionService, Principal]:
    database_url = os.environ.get("PCB_DATABASE_URL")
    subject = os.environ.get("PCB_CLI_SUBJECT")
    if not database_url or not subject:
        raise ValueError("PCB_DATABASE_URL and verified PCB_CLI_SUBJECT are required")
    database = Database(database_url)
    role_repository = PostgresIdentityRepository(database.engine)
    try:
        roles = frozenset(Role(value) for value in role_repository.roles_for_subject(subject))
        if not roles:
            raise PermissionError("CLI subject has no active PolyCodeBench role")
        principal = Principal(subject_id=subject, roles=roles)
        repository = PostgresTaskRepository(database.engine)
        return database, TaskAdmissionService(repository), principal
    except BaseException:
        database.dispose()
        raise


def _task_freeze(args: argparse.Namespace, request_id: str) -> int:
    importer = TaskPackageImporter()
    imported = importer.import_package(args.package)
    report = AdmissionExecutionReport.model_validate(_load_json(args.report))
    if report.package_digest != imported.package_digest or not report.passed:
        raise ValueError("admission report does not pass or does not belong to this package")
    if (
        report.execution_tier != "local_fixture"
        or report.profile_id != "admission-v1-authored-fixture"
    ):
        raise ValueError("production admission requires a trusted worker evidence authority")
    replay = _report(imported.package_digest, validate_authored_fixtures(imported))
    if replay["report_digest"] != report.report_digest:
        raise ValueError("admission evidence differs from the current authored-fixture replay")
    checks = report.checks
    subject = os.environ.get("PCB_CLI_SUBJECT")
    if not subject:
        raise ValueError("verified PCB_CLI_SUBJECT is required")
    manifest = imported.manifest
    admission_report = AdmissionReport.model_validate(
        {
            "schema_version": 1,
            "kind": "admission_report",
            "profile_id": report.profile_id,
            "report_digest": report.report_digest,
            "execution_tier": report.execution_tier,
            "reference_check": checks.reference,
            "faulty_check": checks.faulty,
            "alternative_check": checks.alternative,
            "flakiness_check": checks.flakiness,
            "rights_check": checks.rights,
            "disclosure_check": checks.disclosure,
            "reviewer_id": None,
            "reviewed_at": None,
        }
    )
    task_document = {
        "schema_version": 1,
        "kind": "task_version",
        **manifest.task.model_dump(mode="json", exclude={"methodology_label"}),
        "source": manifest.source.model_dump(mode="json"),
        "visible_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": args.visible_artifact_id,
            "digest": imported.visible_digest,
            "visibility": "internal",
        },
        "hidden_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": args.hidden_artifact_id,
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
        "admission_report": admission_report.model_dump(mode="json"),
    }
    document = TaskVersion.model_validate_json(json.dumps(task_document, ensure_ascii=False))
    database, service, principal = _service()
    try:
        task_version_id = service.freeze_task_version(
            principal=principal,
            document=document,
            execution_report=report,
            manifest_digest=imported.manifest_digest,
            manifest_artifact_id=UUID(args.manifest_artifact_id),
            visible_artifact_id=UUID(args.visible_artifact_id),
            hidden_artifact_id=UUID(args.hidden_artifact_id),
            request_id=request_id,
        )
    finally:
        database.dispose()
    print(
        json.dumps({"task_version_id": str(task_version_id), "digest": canonical_digest(document)})
    )
    return 0


def _taskset(args: argparse.Namespace, request_id: str) -> int:
    database, service, principal = _service()
    try:
        if args.operation == "create":
            document = TaskSet.model_validate(_load_json(args.document))
            task_set_id = service.create_task_set(
                principal=principal,
                document=document,
                manifest_artifact_id=UUID(args.manifest_artifact_id),
                request_id=request_id,
            )
            print(
                json.dumps({"task_set_id": str(task_set_id), "digest": canonical_digest(document)})
            )
            return 0
        service.freeze_task_set(
            principal=principal,
            task_set_id=UUID(args.task_set_id),
            expected_digest=args.digest,
            request_id=request_id,
        )
        print(
            json.dumps({"task_set_id": args.task_set_id, "digest": args.digest, "status": "frozen"})
        )
        return 0
    finally:
        database.dispose()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "release":
        from polycodebench_publication.cli import main as release_main

        return release_main(arguments[1:])
    args = _parser().parse_args(argv)
    request_id = args.request_id or str(uuid4())
    try:
        if args.domain == "task" and args.operation == "import":
            return _local_artifact_import(args)
        if args.domain == "task" and args.operation == "validate":
            return _task_validate(args)
        if args.domain == "task" and args.operation == "freeze":
            return _task_freeze(args, request_id)
        if args.domain == "taskset":
            return _taskset(args, request_id)
        raise ValueError("unsupported task operation")
    except PermissionError as error:
        print(str(error), file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError) as error:
        print(f"validation/configuration error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
