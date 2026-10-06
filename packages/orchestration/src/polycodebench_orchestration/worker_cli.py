"""Opt-in development and identity-gated AWS solve worker entry point.

The command has no active mode by default. Local Docker remains dev-only. ``ec2-run`` requires a
deployed manifest, an STS-verified solve-supervisor role, a pre-registered worker ID and an
explicit dispatch flag before it can claim work or launch disposable guests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from uuid import UUID, uuid4

import boto3  # type: ignore[import-untyped]
import yaml
from polycodebench_core.application_errors import ServiceError
from polycodebench_core.canonical import canonical_document_bytes, canonical_document_digest
from polycodebench_core.deployment import (
    CallerPrincipal,
    DeploymentRefused,
    EnvironmentManifest,
    VerifiedDeployment,
    resolve_deployment,
)
from polycodebench_core.jobs import CapacitySlotSpec, WorkerRegistrationSpec
from polycodebench_core.telemetry import configure_logging
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    artifact,
    artifact_quota,
    capacity_slot,
    config_document,
    worker_registration,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_runner.provider import SshGuestControlChannel
from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from polycodebench_orchestration.solve.control_identity import temporary_control_identity
from polycodebench_orchestration.solve.worker_runtime import (
    SolveWorkerResourceSpec,
    build_ec2_solve_worker,
    build_local_solve_worker,
    load_image_allowlist,
)
from polycodebench_orchestration.worker import WorkerService

SOURCE_ROOT = Path(__file__).resolve().parents[4]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "config").is_dir() else Path("/usr/local")
DEFAULT_RESOURCE_SPEC = ROOT / "config/worker/local-small-resource.json"
DEFAULT_IMAGE_ALLOWLIST = ROOT / "config/worker/local-image-allowlist.json"
DEFAULT_PROTOCOL_DIRECTORY = ROOT / "config/protocols"
DEFAULT_BUDGET_PROFILES = ROOT / "config/budgets/pilot-v1.yaml"
DEFAULT_SANDBOX_STATE = ROOT / ".cache/local-solve-worker"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-worker")
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser(
        "local-register", help="register a bounded development-only Docker worker"
    )
    register.add_argument("--resource-spec", type=Path, default=DEFAULT_RESOURCE_SPEC)
    register.add_argument("--slots", type=int, default=1)
    register.add_argument("--workload-identity", default="local-solve-worker-1")

    run = commands.add_parser("local-run", help="run one solve job or watch the local queue")
    run.add_argument("--worker-id", type=UUID)
    run.add_argument("--watch", action="store_true")
    run.add_argument("--image-allowlist", type=Path, default=DEFAULT_IMAGE_ALLOWLIST)
    run.add_argument("--protocol-directory", type=Path, default=DEFAULT_PROTOCOL_DIRECTORY)
    run.add_argument("--budget-profiles", type=Path, default=DEFAULT_BUDGET_PROFILES)
    run.add_argument("--sandbox-state", type=Path, default=DEFAULT_SANDBOX_STATE)

    ec2_run = commands.add_parser(
        "ec2-run", help="run a pre-registered worker using the verified disposable-VM provider"
    )
    ec2_run.add_argument("--worker-id", type=UUID)
    ec2_run.add_argument("--watch", action="store_true")
    ec2_run.add_argument("--image-allowlist", type=Path, required=True)
    ec2_run.add_argument("--protocol-directory", type=Path, default=DEFAULT_PROTOCOL_DIRECTORY)
    ec2_run.add_argument("--budget-profiles", type=Path, default=DEFAULT_BUDGET_PROFILES)
    ec2_run.add_argument("--known-hosts", type=Path, required=True)

    ec2_register = commands.add_parser(
        "ec2-register", help="register one reviewed solve-worker capacity plan without dispatch"
    )
    ec2_register.add_argument("--resource-spec", type=Path, required=True)
    ec2_register.add_argument("--image-allowlist", type=Path, required=True)
    ec2_register.add_argument("--slots", type=int, default=1)
    ec2_register.add_argument("--workload-identity")
    return parser


def _development_only() -> None:
    if os.environ.get("PCB_ENVIRONMENT") != "dev":
        raise ValueError("this worker command is restricted to PCB_ENVIRONMENT=dev")


def _worker_database() -> Database:
    url = os.environ.get("PCB_WORKER_DATABASE_URL") or os.environ.get("PCB_DATABASE_URL")
    if not url:
        raise ValueError("PCB_WORKER_DATABASE_URL or PCB_DATABASE_URL is required")
    return Database(url)


def _object_store(*, region_name: str = "us-east-1") -> S3ArtifactStore:
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    buckets = {
        "hidden": os.environ.get("PCB_BUCKET_HIDDEN"),
        "internal": os.environ.get("PCB_BUCKET_INTERNAL"),
        "public": os.environ.get("PCB_BUCKET_PUBLIC"),
    }
    if not endpoint or any(not value for value in buckets.values()):
        raise ValueError("object-store endpoint and bucket names are required")
    return S3ArtifactStore(
        endpoint_url=endpoint,
        buckets={key: str(value) for key, value in buckets.items()},
        region_name=region_name,
    )


def _register_local(resource_path: Path, *, slots: int, workload_identity: str) -> int:
    _development_only()
    if os.environ.get("PCB_LOCAL_WORKER_SETUP_ENABLED") != "true":
        raise ValueError("set PCB_LOCAL_WORKER_SETUP_ENABLED=true for explicit local registration")
    if not 1 <= slots <= 8:
        raise ValueError("local worker slots must be in [1,8]")
    resource = SolveWorkerResourceSpec.model_validate_json(
        resource_path.read_text(encoding="utf-8"), strict=True
    )
    allowlist = load_image_allowlist(DEFAULT_IMAGE_ALLOWLIST)
    if allowlist.get(resource.image) != resource.image_digest:
        raise ValueError("resource image is not in the pinned local allowlist")
    admin_url = os.environ.get("PCB_MIGRATION_DATABASE_URL")
    if not admin_url:
        raise ValueError("PCB_MIGRATION_DATABASE_URL is required to register local worker config")

    database = _worker_database()
    admin_database = Database(admin_url)
    try:
        object_store = _object_store()
        with database.engine.connect() as connection:
            existing = (
                connection.execute(
                    select(
                        worker_registration.c.id,
                        worker_registration.c.status,
                        worker_registration.c.lane,
                        worker_registration.c.hardware_class,
                        worker_registration.c.driver_identity,
                        worker_registration.c.allowed_queue_classes,
                        worker_registration.c.allowed_resource_classes,
                        capacity_slot.c.slot_key,
                        capacity_slot.c.resource_class,
                        config_document.c.digest,
                    )
                    .select_from(
                        worker_registration.outerjoin(
                            capacity_slot, capacity_slot.c.worker_id == worker_registration.c.id
                        ).outerjoin(
                            config_document,
                            config_document.c.id == capacity_slot.c.resource_spec_config_id,
                        )
                    )
                    .where(worker_registration.c.workload_identity == workload_identity)
                    .order_by(capacity_slot.c.slot_key)
                )
                .mappings()
                .all()
            )
        if existing:
            first = existing[0]
            if first["status"] != "active":
                raise ValueError("local worker identity already exists but is not active")
            if (
                first["lane"] != "solve"
                or first["hardware_class"] != "local-docker-development"
                or first["driver_identity"] != "LocalDockerSandboxProvider"
                or first["allowed_queue_classes"] != ["solve"]
                or first["allowed_resource_classes"] != [resource.resource_class]
                or len(existing) != slots
                or any(
                    row["resource_class"] != resource.resource_class
                    or row["digest"] != canonical_document_digest(resource)
                    for row in existing
                )
            ):
                raise ValueError("active local worker identity has a different resource plan")
            worker_id = UUID(str(first["id"]))
            _emit({"worker_id": str(worker_id), "registered": False, "status": "active"})
            return 0

        body = canonical_document_bytes(resource)
        digest = canonical_document_digest(resource)
        with admin_database.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"pcb.local-worker-resource:{digest}"},
            )
            config_row = connection.execute(
                select(config_document.c.id, config_document.c.canonical_artifact_id).where(
                    config_document.c.kind == "resource_spec",
                    config_document.c.digest == digest,
                )
            ).one_or_none()
            if config_row is None:
                existing_artifact = connection.execute(
                    select(artifact.c.id, artifact.c.status).where(
                        artifact.c.visibility == "internal",
                        artifact.c.encryption_domain == "worker-config",
                        artifact.c.content_digest == digest,
                    )
                ).one_or_none()
                if existing_artifact is not None:
                    if existing_artifact.status != "verified":
                        raise ValueError("existing worker resource artifact is not verified")
                    verified_artifact_id = existing_artifact.id
                else:
                    object_store.ensure_buckets()
                    artifact_repository = ArtifactRepository(
                        database.engine, object_store, max_upload_bytes=512 * 1024**2
                    )
                    upload_id = artifact_repository.begin_upload(
                        owner="local-worker-registration",
                        visibility="internal",
                        encryption_domain="worker-config",
                        expected_digest=digest,
                        expected_size=len(body),
                        media_type="application/json",
                    )
                    artifact_repository.upload(
                        upload_id=upload_id, owner="local-worker-registration", body=body
                    )
                    verified_artifact_id = artifact_repository.finalize(
                        upload_id=upload_id, owner="local-worker-registration"
                    )
                config_id = uuid4()
                connection.execute(
                    insert(config_document).values(
                        id=config_id,
                        kind="resource_spec",
                        version_label=digest[7:19],
                        digest=digest,
                        canonical_artifact_id=verified_artifact_id,
                        schema_version=1,
                        document=resource.model_dump(mode="json"),
                    )
                )
            else:
                source_artifact = connection.execute(
                    select(
                        artifact.c.status,
                        artifact.c.visibility,
                        artifact.c.encryption_domain,
                        artifact.c.content_digest,
                    ).where(artifact.c.id == config_row.canonical_artifact_id)
                ).one_or_none()
                if (
                    source_artifact is None
                    or source_artifact.status != "verified"
                    or source_artifact.visibility != "internal"
                    or source_artifact.encryption_domain != "worker-config"
                    or source_artifact.content_digest != digest
                ):
                    raise ValueError("existing worker resource config has no verified artifact")
                config_id = config_row.id
        registration = WorkerRegistrationSpec(
            workload_identity=workload_identity,
            lane="solve",
            hardware_class="local-docker-development",
            driver_identity="LocalDockerSandboxProvider",
            allowed_queue_classes=("solve",),
            allowed_resource_classes=(resource.resource_class,),
            resource_spec_config_id=config_id,
            slots=tuple(
                CapacitySlotSpec(
                    slot_key=f"slot-{index}",
                    resource_class=resource.resource_class,
                    resource_spec_config_id=config_id,
                )
                for index in range(slots)
            ),
        )
        worker_id = PostgresJobRepository(database.engine).register_worker(registration)
        _emit(
            {
                "worker_id": str(worker_id),
                "resource_class": resource.resource_class,
                "slots": slots,
                "registered": True,
                "dispatch_enabled": False,
            }
        )
        return 0
    finally:
        admin_database.dispose()
        database.dispose()


def _run_local(worker_id: UUID | None, *, watch: bool, args: argparse.Namespace) -> int:
    _development_only()
    if os.environ.get("PCB_WORKER_DISPATCH_ENABLED") != "true":
        raise ValueError("set PCB_WORKER_DISPATCH_ENABLED=true to allow local queue claims")
    service_identity = os.environ.get("PCB_SERVICE_IDENTITY")
    if not service_identity:
        raise ValueError("PCB_SERVICE_IDENTITY is required for worker audit attribution")
    selected_worker = worker_id or UUID(os.environ.get("PCB_WORKER_ID", ""))
    allowlist = load_image_allowlist(args.image_allowlist)
    database = _worker_database()
    try:
        worker = build_local_solve_worker(
            worker_id=selected_worker,
            scheduler_engine=database.engine,
            solve_engine=database.engine,
            gateway_engine=database.engine,
            artifact_engine=database.engine,
            object_store=_object_store(),
            image_allowlist=allowlist,
            state_dir=args.sandbox_state,
            protocol_directory=args.protocol_directory,
            budget_profile_file=args.budget_profiles,
            secret_namespace=os.environ.get("PCB_MODEL_SECRET_NAMESPACE", "models"),
        )
        if not watch:
            claimed = asyncio.run(worker.run_once())
            _emit({"worker_id": str(selected_worker), "claimed": claimed})
            return 0
        _serve_worker(worker)
        return 0
    finally:
        database.dispose()


def _read_aws_manifest(required_role: str) -> tuple[EnvironmentManifest, VerifiedDeployment]:
    path = Path(os.environ.get("PCB_ENV_MANIFEST", ""))
    if not path.is_file():
        raise ValueError("PCB_ENV_MANIFEST must name the reviewed deployed environment manifest")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise ValueError("AWS solve worker manifest cannot be read") from None
    if not isinstance(document, dict):
        raise ValueError("AWS solve worker manifest must be a YAML object")
    manifest = EnvironmentManifest.model_validate(document)
    environment = os.environ.get("PCB_ENVIRONMENT")
    role = os.environ.get("PCB_VERIFIED_ROLE")
    principal = os.environ.get("PCB_SERVICE_IDENTITY")
    if (
        manifest.status != "deployed"
        or manifest.environment not in {"staging", "production"}
        or environment != manifest.environment
        or os.environ.get("PCB_VERIFIED_ENVIRONMENT") != manifest.environment
        or role != required_role
        or os.environ.get("PCB_VERIFIED_BY") != "aws-sts"
        or not principal
    ):
        raise ValueError(
            f"AWS worker command requires the verified deployed {required_role} identity"
        )
    try:
        if not manifest.identity.region:
            raise DeploymentRefused("manifest has no AWS region")
        response = boto3.client("sts", region_name=manifest.identity.region).get_caller_identity()
        caller = CallerPrincipal(
            account_id=str(response["Account"]),
            arn=str(response["Arn"]),
            verified_by="aws-sts",
        )
        if caller.arn != principal:
            raise DeploymentRefused("process identity differs from the STS principal")
        deployment = resolve_deployment(
            manifest,
            claimed_environment=environment,
            claimed_role=required_role,
            caller=caller,
        )
    except DeploymentRefused:
        raise ValueError("AWS worker principal does not match its manifest") from None
    return manifest, deployment


def _register_ec2(args: argparse.Namespace) -> int:
    if os.environ.get("PCB_AWS_WORKER_SETUP_ENABLED") != "true":
        raise ValueError(
            "set PCB_AWS_WORKER_SETUP_ENABLED=true for explicit AWS worker registration"
        )
    if os.environ.get("PCB_WORKER_DISPATCH_ENABLED") == "true":
        raise ValueError("worker registration must run separately from queue dispatch")
    manifest, _deployment = _read_aws_manifest("migrator")
    resource = SolveWorkerResourceSpec.model_validate_json(
        args.resource_spec.read_text(encoding="utf-8"), strict=True
    )
    allowlist = load_image_allowlist(args.image_allowlist)
    if allowlist.get(resource.image) != resource.image_digest:
        raise ValueError("resource image is not in the reviewed candidate image allowlist")
    maximum_slots = min(8, manifest.capacity.max_concurrency)
    if not 1 <= args.slots <= maximum_slots:
        raise ValueError(f"AWS solve worker slots must be in [1,{maximum_slots}]")
    workload_identity = args.workload_identity or (
        f"{manifest.environment}-solve-{resource.resource_class}"
    )
    if not workload_identity.isascii() or not 1 <= len(workload_identity) <= 255:
        raise ValueError("AWS solve worker workload identity is invalid")
    if not manifest.identity.region:
        raise ValueError("AWS solve worker manifest has no region")

    database = _worker_database()
    try:
        store = _object_store(region_name=manifest.identity.region)
        endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
        buckets = {
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC"),
        }
        if endpoint != manifest.object_store.endpoint or buckets != {
            "hidden": manifest.object_store.bucket_hidden,
            "internal": manifest.object_store.bucket_internal,
            "public": manifest.object_store.bucket_public,
        }:
            raise ValueError("object-store settings differ from the deployed manifest")

        digest = canonical_document_digest(resource)
        artifact_repository = ArtifactRepository(
            database.engine, store, max_upload_bytes=512 * 1024**2
        )
        # ArtifactRepository uses its own transactions; commit the fixed quota row first so
        # begin_upload can reserve bytes through a separate connection.
        with database.engine.begin() as connection:
            connection.execute(
                postgres_insert(artifact_quota)
                .values(
                    visibility="internal",
                    encryption_domain="worker-config",
                    max_bytes=64 * 1024**2,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        artifact_quota.c.visibility,
                        artifact_quota.c.encryption_domain,
                    ]
                )
            )
        with database.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"pcb.worker-resource:{digest}"},
            )
            config_row = connection.execute(
                select(config_document.c.id, config_document.c.canonical_artifact_id).where(
                    config_document.c.kind == "resource_spec",
                    config_document.c.digest == digest,
                )
            ).one_or_none()
            if config_row is None:
                existing_artifact = connection.execute(
                    select(artifact.c.id, artifact.c.status).where(
                        artifact.c.visibility == "internal",
                        artifact.c.encryption_domain == "worker-config",
                        artifact.c.content_digest == digest,
                    )
                ).one_or_none()
                if existing_artifact is not None:
                    if existing_artifact.status != "verified":
                        raise ValueError("existing worker resource artifact is not verified")
                    verified_artifact_id = existing_artifact.id
                else:
                    body = canonical_document_bytes(resource)
                    upload_id = artifact_repository.begin_upload(
                        owner="ec2-worker-registration",
                        visibility="internal",
                        encryption_domain="worker-config",
                        expected_digest=digest,
                        expected_size=len(body),
                        media_type="application/json",
                    )
                    artifact_repository.upload(
                        upload_id=upload_id,
                        owner="ec2-worker-registration",
                        body=body,
                    )
                    verified_artifact_id = artifact_repository.finalize(
                        upload_id=upload_id,
                        owner="ec2-worker-registration",
                    )
                config_id = uuid4()
                connection.execute(
                    insert(config_document).values(
                        id=config_id,
                        kind="resource_spec",
                        version_label=digest[7:19],
                        digest=digest,
                        canonical_artifact_id=verified_artifact_id,
                        schema_version=1,
                        document=resource.model_dump(mode="json"),
                    )
                )
            else:
                source_artifact = connection.execute(
                    select(
                        artifact.c.status,
                        artifact.c.visibility,
                        artifact.c.encryption_domain,
                        artifact.c.content_digest,
                    ).where(artifact.c.id == config_row.canonical_artifact_id)
                ).one_or_none()
                if (
                    source_artifact is None
                    or source_artifact.status != "verified"
                    or source_artifact.visibility != "internal"
                    or source_artifact.encryption_domain != "worker-config"
                    or source_artifact.content_digest != digest
                ):
                    raise ValueError("existing worker resource config has no verified artifact")
                config_id = config_row.id

        registration = WorkerRegistrationSpec(
            workload_identity=workload_identity,
            lane="solve",
            hardware_class="aws-ec2-disposable-vm-v1",
            driver_identity="Ec2VmSandboxProvider",
            allowed_queue_classes=("solve",),
            allowed_resource_classes=(resource.resource_class,),
            resource_spec_config_id=config_id,
            slots=tuple(
                CapacitySlotSpec(
                    slot_key=f"slot-{index}",
                    resource_class=resource.resource_class,
                    resource_spec_config_id=config_id,
                )
                for index in range(args.slots)
            ),
        )
        with database.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"pcb.worker-capacity:{manifest.environment}"},
            )
            existing = (
                connection.execute(
                    select(
                        worker_registration.c.id,
                        worker_registration.c.status,
                        worker_registration.c.lane,
                        worker_registration.c.hardware_class,
                        worker_registration.c.driver_identity,
                        worker_registration.c.allowed_queue_classes,
                        worker_registration.c.allowed_resource_classes,
                        capacity_slot.c.resource_class,
                        config_document.c.digest,
                    )
                    .select_from(
                        worker_registration.outerjoin(
                            capacity_slot, capacity_slot.c.worker_id == worker_registration.c.id
                        ).outerjoin(
                            config_document,
                            config_document.c.id == capacity_slot.c.resource_spec_config_id,
                        )
                    )
                    .where(worker_registration.c.workload_identity == workload_identity)
                    .order_by(capacity_slot.c.slot_key)
                )
                .mappings()
                .all()
            )
            if existing:
                first = existing[0]
                if (
                    first["status"] != "active"
                    or first["lane"] != "solve"
                    or first["hardware_class"] != "aws-ec2-disposable-vm-v1"
                    or first["driver_identity"] != "Ec2VmSandboxProvider"
                    or first["allowed_queue_classes"] != ["solve"]
                    or first["allowed_resource_classes"] != [resource.resource_class]
                    or len(existing) != args.slots
                    or any(
                        row["resource_class"] != resource.resource_class or row["digest"] != digest
                        for row in existing
                    )
                ):
                    raise ValueError("AWS worker identity already has a different registration")
                worker_id = UUID(str(first["id"]))
                registered = False
            else:
                registered_slots = connection.execute(
                    select(func.count())
                    .select_from(
                        worker_registration.join(
                            capacity_slot,
                            capacity_slot.c.worker_id == worker_registration.c.id,
                        )
                    )
                    .where(capacity_slot.c.state != "disabled")
                ).scalar_one()
                if registered_slots + args.slots > manifest.capacity.max_concurrency:
                    raise ValueError("AWS worker registration exceeds the environment capacity cap")
                worker_id = PostgresJobRepository(database.engine).register_worker(registration)
                registered = True
        _emit(
            {
                "worker_id": str(worker_id),
                "workload_identity": workload_identity,
                "resource_class": resource.resource_class,
                "slots": args.slots,
                "registered": registered,
                "dispatch_enabled": False,
            }
        )
        return 0
    finally:
        database.dispose()


def _run_ec2(worker_id: UUID | None, *, watch: bool, args: argparse.Namespace) -> int:
    if os.environ.get("PCB_WORKER_DISPATCH_ENABLED") != "true":
        raise ValueError("set PCB_WORKER_DISPATCH_ENABLED=true for explicit AWS queue dispatch")
    service_identity = os.environ.get("PCB_SERVICE_IDENTITY")
    if not service_identity:
        raise ValueError("PCB_SERVICE_IDENTITY is required for worker audit attribution")
    manifest, deployment = _read_aws_manifest("solve-supervisor")
    selected_worker = worker_id or UUID(os.environ.get("PCB_WORKER_ID", ""))
    if not args.known_hosts.is_file():
        raise ValueError("the pinned guest known-hosts file is required")
    if not args.protocol_directory.is_dir() or not args.budget_profiles.is_file():
        raise ValueError("solve protocol and budget configuration files are required")
    allowlist = load_image_allowlist(args.image_allowlist)
    if not manifest.identity.region:
        raise ValueError("AWS solve worker manifest has no region")

    database = _worker_database()
    try:
        store = _object_store(region_name=manifest.identity.region)
        endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
        buckets = {
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC"),
        }
        if endpoint != manifest.object_store.endpoint or buckets != {
            "hidden": manifest.object_store.bucket_hidden,
            "internal": manifest.object_store.bucket_internal,
            "public": manifest.object_store.bucket_public,
        }:
            raise ValueError("object-store settings differ from the deployed manifest")

        ec2_client = boto3.client("ec2", region_name=manifest.identity.region)
        sts_client = boto3.client("sts", region_name=manifest.identity.region)
        secretsmanager_client = boto3.client("secretsmanager", region_name=manifest.identity.region)
        with temporary_control_identity(
            secrets_manager_client=secretsmanager_client,
            manifest=manifest,
            supervisor_role="solve-supervisor",
        ) as identity_file:
            control_channel = SshGuestControlChannel(
                username=manifest.sandbox.control_user,
                identity_file=identity_file,
                known_hosts_file=args.known_hosts,
            )
            worker = build_ec2_solve_worker(
                worker_id=selected_worker,
                manifest=manifest,
                deployment=deployment,
                scheduler_engine=database.engine,
                solve_engine=database.engine,
                gateway_engine=database.engine,
                artifact_engine=database.engine,
                object_store=store,
                candidate_image_digests=allowlist,
                protocol_directory=args.protocol_directory,
                budget_profile_file=args.budget_profiles,
                ec2_client=ec2_client,
                sts_client=sts_client,
                secretsmanager_client=secretsmanager_client,
                control_channel=control_channel,
            )
            if not watch:
                claimed = asyncio.run(worker.run_once())
                _emit({"worker_id": str(selected_worker), "claimed": claimed})
                return 0
            _serve_worker(worker)
            return 0
    finally:
        database.dispose()


def _serve_worker(worker: WorkerService) -> None:
    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:
                signal.signal(signum, lambda _signal, _frame: loop.call_soon_threadsafe(stop.set))
        await worker.run_until_stopped(stop)

    asyncio.run(serve())


def _emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    identity = os.environ.get("PCB_SERVICE_IDENTITY") or "local-unverified"
    configure_logging(
        environment=os.environ.get("PCB_ENVIRONMENT", "dev"),
        role="solve-worker",
        service_identity=identity,
    )
    try:
        if args.command == "local-register":
            return _register_local(
                args.resource_spec,
                slots=args.slots,
                workload_identity=args.workload_identity,
            )
        if args.command == "local-run":
            return _run_local(args.worker_id, watch=args.watch, args=args)
        if args.command == "ec2-run":
            return _run_ec2(args.worker_id, watch=args.watch, args=args)
        return _register_ec2(args)
    except ServiceError as error:
        print(f"worker error: {error.code}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("worker configuration or operation failed", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"worker failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
