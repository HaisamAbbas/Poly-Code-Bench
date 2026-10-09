"""``pcb-ops``: deployment verification, recovery rehearsals and operator drills.

Exit codes: 0 success; 1 check failed (the operation ran and found a problem); 2 invalid
invocation/configuration; 3 not deployable (unresolved deployment inputs); 4 refused
(identity/environment mismatch). Output is one JSON document on stdout; secrets never appear.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import yaml
from polycodebench_core.application_errors import InvalidState
from polycodebench_core.deployment import DeploymentRefused, placeholders
from polycodebench_core.telemetry import REQUIRED_METRICS, MetricsRegistry, configure_logging

from polycodebench_operations import environments, migrations

if TYPE_CHECKING:
    from polycodebench_persistence.database import Database
    from polycodebench_persistence.object_store import S3ArtifactStore

    from polycodebench_operations.recovery import EnvironmentSource

REPO_ROOT = environments.REPO_ROOT
ALERT_RULES = REPO_ROOT / "infra" / "observability" / "prometheus" / "alerts.yaml"
REHEARSAL_CONFIG = REPO_ROOT / "config" / "operations" / "rehearsal-local.yaml"


def _emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, indent=2, sort_keys=True, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-ops", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="deployment readiness of one environment")
    doctor.add_argument(
        "--profile", "--env", dest="env", required=True, choices=environments.ENVIRONMENTS
    )

    env = commands.add_parser("env", help="environment manifests")
    env_sub = env.add_subparsers(dest="action", required=True)
    env_sub.add_parser("validate", help="validate all manifests and cross-environment separation")
    reconcile = env_sub.add_parser("reconcile", help="compare a manifest with terraform output")
    reconcile.add_argument("--env", required=True, choices=environments.ENVIRONMENTS)
    reconcile.add_argument("--terraform-output", type=Path, required=True)

    identity = commands.add_parser("identity", help="startup identity guard")
    identity_sub = identity.add_subparsers(dest="action", required=True)
    verify = identity_sub.add_parser("verify", help="verify this process's principal")
    verify.add_argument("--manifest", type=Path, default=None)
    verify.add_argument("--role", default=None, help="claimed role (default: PCB_ROLE)")
    verify.add_argument(
        "--exec",
        dest="exec_",
        action="store_true",
        help="exec the command after `--` once verified",
    )
    verify.add_argument("service_command", nargs=argparse.REMAINDER, metavar="-- COMMAND")

    migrate = commands.add_parser("migrate", help="expand/contract migration checks")
    migrate_sub = migrate.add_subparsers(dest="action", required=True)
    migrate_sub.add_parser("check", help="static expand-only check since the released schema")
    rehearse = migrate_sub.add_parser("rehearse", help="empty->head and previous->head")
    rehearse.add_argument("--admin-url", default=os.environ.get("PCB_MIGRATION_ADMIN_URL"))
    rehearse.add_argument(
        "--persistence-root",
        type=Path,
        default=migrations.PERSISTENCE,
        help="packages/persistence of the tree to rehearse (e.g. a release worktree)",
    )
    upgrade = migrate_sub.add_parser("upgrade", help="expand-only check, then alembic upgrade")
    upgrade.add_argument("--target", default="head")

    workers = commands.add_parser("workers", help="worker lifecycle")
    workers_sub = workers.add_subparsers(dest="action", required=True)
    drain = workers_sub.add_parser("drain", help="mark workers draining (leases fence the rest)")
    drain.add_argument("worker_ids", nargs="+")

    orphans = commands.add_parser("orphans", help="orphaned guest cleanup")
    orphans_sub = orphans.add_subparsers(dest="action", required=True)
    sweep = orphans_sub.add_parser("sweep", help="reclaim expired guests and verify")
    sweep.add_argument("--provider", choices=("local", "ec2"), default="local")
    sweep.add_argument("--provider-id", default="local-default")
    sweep.add_argument(
        "--image",
        action="append",
        default=[],
        help="approved image reference (local provider allowlist)",
    )
    sweep.add_argument("--grace-seconds", type=int, default=600)
    sweep.add_argument("--environment", default=os.environ.get("PCB_ENVIRONMENT"))
    sweep.add_argument(
        "--dry-run",
        action="store_true",
        help="report expired guests and set the alert metrics; reclaim nothing",
    )

    artifacts = commands.add_parser("artifacts", help="artifact lifecycle")
    artifacts_sub = artifacts.add_subparsers(dest="action", required=True)
    artifacts_sub.add_parser("collect-garbage", help="expire abandoned uploads and orphans")

    backup = commands.add_parser("backup", help="environment backups")
    backup_sub = backup.add_subparsers(dest="action", required=True)
    create = backup_sub.add_parser("create", help="back up database, objects and publication")
    create.add_argument("--config", type=Path, default=REHEARSAL_CONFIG)
    create.add_argument("--out", type=Path, required=True)

    restore = commands.add_parser("restore", help="isolated restore rehearsal (E2E-42)")
    restore_sub = restore.add_subparsers(dest="action", required=True)
    rehearse_restore = restore_sub.add_parser("rehearse", help="restore, verify, replay, rebuild")
    rehearse_restore.add_argument("--backup", type=Path, required=True)
    rehearse_restore.add_argument("--config", type=Path, default=REHEARSAL_CONFIG)
    rehearse_restore.add_argument("--work-dir", type=Path, default=None)
    rehearse_restore.add_argument("--evidence", type=Path, default=None)
    rehearse_restore.add_argument(
        "--target", choices=("local-docker", "aws-pitr"), default="local-docker"
    )

    verify_restore = restore_sub.add_parser(
        "verify", help="verify an already-restored environment (e.g. after AWS PITR)"
    )
    verify_restore.add_argument("--database-url", required=True)
    verify_restore.add_argument(
        "--object-store-endpoint",
        default=None,
        help="omit for AWS S3 via the restore-operator credentials",
    )
    verify_restore.add_argument("--bucket-hidden", required=True)
    verify_restore.add_argument("--bucket-internal", required=True)
    verify_restore.add_argument("--bucket-public", required=True)
    verify_restore.add_argument("--release-store", type=Path, required=True)
    verify_restore.add_argument("--keyring", type=Path, required=True)
    verify_restore.add_argument("--board", default="local:board")
    verify_restore.add_argument("--replay-count", type=int, default=10)
    verify_restore.add_argument("--seed", default="pcb-restore-rehearsal-v1")
    verify_restore.add_argument("--evidence", type=Path, default=None)

    keys = commands.add_parser("keys", help="publication signing keys")
    keys_sub = keys.add_subparsers(dest="action", required=True)
    rotate = keys_sub.add_parser("rotate", help="activate a new key; retire the previous one")
    rotate.add_argument("--keyring", type=Path, required=True)
    rotate.add_argument("--new-key-id", required=True)
    rotate.add_argument("--private-key-out", type=Path, required=True)
    revoke = keys_sub.add_parser("revoke", help="mark a compromised key revoked")
    revoke.add_argument("--keyring", type=Path, required=True)
    revoke.add_argument("--key-id", required=True)
    revoke.add_argument("--reason", required=True)
    verify_release = keys_sub.add_parser("verify", help="verify a release manifest")
    verify_release.add_argument("--keyring", type=Path, required=True)
    verify_release.add_argument("--manifest", type=Path, required=True)

    releases = commands.add_parser("releases", help="public release catalog")
    releases_sub = releases.add_subparsers(dest="action", required=True)
    sync_releases = releases_sub.add_parser(
        "sync-publication", help="verify and mirror signed public release snapshots"
    )
    sync_releases.add_argument("--store", type=Path, required=True)
    sync_releases.add_argument("--keyring", type=Path, required=True)
    sync_releases.add_argument("--target", required=True)
    sync_releases.add_argument("--source-target", default="local:board")
    build_live = releases_sub.add_parser(
        "build-live",
        help="build unranked live_exploratory release documents from persisted scorecards",
    )
    build_live.add_argument(
        "--run-id", type=UUID, action="append", required=True, help="completed run (repeatable)"
    )
    build_live.add_argument("--output-dir", type=Path, required=True)

    alerts = commands.add_parser("alerts", help="alert rules")
    alerts_sub = alerts.add_subparsers(dest="action", required=True)
    alerts_sub.add_parser("check", help="rules reference only catalogued metrics and runbooks")
    return parser


# ----------------------------------------------------------------------------- commands


def _doctor(env: str) -> int:
    path = environments.manifest_path(env)
    manifest = environments.load_manifest(path)
    unresolved = placeholders(manifest)
    report: dict[str, Any] = {
        "environment": env,
        "manifest": str(path.relative_to(REPO_ROOT)),
        "status": manifest.status,
        "isolation_tier": manifest.isolation_tier,
        "unresolved_inputs": unresolved,
        "checks": {},
    }
    _, violations = environments.validate_all()
    report["checks"]["separation"] = violations or "pass"
    try:
        migration_problems = migrations.check_expand_only().violations
    except ValueError as error:
        migration_problems = [str(error)]
    report["checks"]["migrations_expand_only"] = migration_problems or "pass"
    if manifest.identity.provider == "aws":
        report["checks"]["identity"] = (
            "requires a verified AWS principal: run `pcb-ops identity verify` inside the service"
        )
    deployable = (
        manifest.status == "deployed"
        and not unresolved
        and not violations
        and not migration_problems
    )
    report["deployable"] = deployable
    _emit(report)
    if violations:
        return 1
    return 0 if deployable else 3


def _identity_verify(args: argparse.Namespace) -> int:
    from polycodebench_operations import identity

    manifest_file = args.manifest or Path(os.environ.get("PCB_ENV_MANIFEST", ""))
    claimed_env = os.environ.get("PCB_ENVIRONMENT", "")
    if not manifest_file or not manifest_file.is_file():
        if claimed_env in environments.ENVIRONMENTS:
            manifest_file = environments.manifest_path(claimed_env)
        else:
            print("PCB_ENV_MANIFEST or a known PCB_ENVIRONMENT is required", file=sys.stderr)
            return 2
    role = args.role or os.environ.get("PCB_ROLE", "")
    manifest = environments.load_manifest(manifest_file)
    try:
        deployment = identity.verify(
            manifest, claimed_environment=claimed_env or manifest.environment, claimed_role=role
        )
    except DeploymentRefused as error:
        print(f"identity refused: {error}", file=sys.stderr)
        return 4
    command = [part for part in args.service_command if part != "--"]
    if args.exec_:
        identity.exec_service(command, deployment)
        return 0  # pragma: no cover - exec does not return
    _emit({**identity.verified_environment(deployment), "principal": deployment.principal})
    return 0


def _migrate(args: argparse.Namespace) -> int:
    if args.action == "check":
        try:
            report = migrations.check_expand_only()
        except ValueError as error:
            _emit({"violations": [str(error)]})
            return 1
        _emit(report.__dict__)
        return 1 if report.violations else 0
    if args.action == "rehearse":
        if not args.admin_url:
            print("--admin-url or PCB_MIGRATION_ADMIN_URL is required", file=sys.stderr)
            return 2
        result = migrations.rehearse(args.admin_url, persistence_root=args.persistence_root)
        _emit(result)
        return 0 if result["passed"] else 1
    report = migrations.check_expand_only()
    if report.violations:
        _emit(
            {
                "refused": "destructive migration without an approved contract plan",
                **report.__dict__,
            }
        )
        return 1
    url = os.environ.get("PCB_MIGRATION_DATABASE_URL")
    if not url:
        print("PCB_MIGRATION_DATABASE_URL is required", file=sys.stderr)
        return 2
    upgraded = migrations.alembic(url, "upgrade", args.target)
    _emit({"target": args.target, "returncode": upgraded.returncode})
    return 0 if upgraded.returncode == 0 else 1


def _database() -> Database:
    from polycodebench_persistence.database import Database

    url = os.environ.get("PCB_DATABASE_URL")
    if not url:
        raise SystemExit("PCB_DATABASE_URL is required")
    return Database(url)


def _object_store() -> S3ArtifactStore:
    from polycodebench_persistence.object_store import S3ArtifactStore

    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    if not endpoint:
        raise SystemExit("PCB_OBJECT_STORE_ENDPOINT is required")
    return S3ArtifactStore.from_environment(
        endpoint_url=endpoint,
        buckets={
            "hidden": os.environ["PCB_BUCKET_HIDDEN"],
            "internal": os.environ["PCB_BUCKET_INTERNAL"],
            "public": os.environ["PCB_BUCKET_PUBLIC"],
        },
    )


def _workers_drain(worker_ids: list[str]) -> int:
    from uuid import UUID

    from polycodebench_persistence.jobs import PostgresJobRepository

    database = _database()
    try:
        repository = PostgresJobRepository(database.engine)
        changed = {
            worker: repository.set_worker_status(UUID(worker), status="draining")
            for worker in worker_ids
        }
    finally:
        database.dispose()
    _emit(
        {
            "draining": changed,
            "note": "draining workers take no new claims; in-flight "
            "leases complete or expire and are fenced by the reaper",
        }
    )
    return 0 if all(changed.values()) else 1


def _orphans(args: argparse.Namespace, registry: MetricsRegistry) -> int:
    from polycodebench_operations import orphans

    if args.provider == "ec2":
        import boto3  # type: ignore[import-untyped]

        if not args.environment:
            print("--environment is required for the EC2 sweep", file=sys.stderr)
            return 2
        report = orphans.sweep_ec2(
            boto3.client("ec2"),
            environment=args.environment,
            grace_seconds=args.grace_seconds,
            registry=registry,
            settle_seconds=5.0,
            dry_run=args.dry_run,
        )
    else:
        from polycodebench_runner.provider import LocalDockerSandboxProvider

        images = {image: "sha256:" + image.rsplit("@sha256:", 1)[-1] for image in args.image}
        if not images:
            print("--image is required for the local provider allowlist", file=sys.stderr)
            return 2
        provider = LocalDockerSandboxProvider(
            allowed_images=images,
            state_dir=REPO_ROOT / ".cache" / "ops-orphan-sweep",
            provider_id=args.provider_id,
        )
        report = orphans.sweep_local(
            provider, grace_seconds=args.grace_seconds, registry=registry, dry_run=args.dry_run
        )
    _emit(
        {
            **report.as_dict(),
            "dry_run": args.dry_run,
            "alert_firing": report.over_alert_threshold > 0,
        }
    )
    return 0 if not report.remaining_expired else 1


def _collect_garbage() -> int:
    from polycodebench_persistence.artifacts import ArtifactRepository

    database = _database()
    try:
        collected = ArtifactRepository(
            database.engine, _object_store(), max_upload_bytes=0
        ).collect_garbage()
    finally:
        database.dispose()
    _emit({"collected": collected})
    return 0


def _required_rehearsal_env(source: dict[str, Any], key: str) -> str:
    name = source[key]
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required; load the ignored local .env first")
    return value


def _rehearsal_source(config_path: Path) -> tuple[dict[str, Any], EnvironmentSource]:
    from polycodebench_operations.recovery import EnvironmentSource

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source = config["source"]
    return config, EnvironmentSource(
        postgres_container=source["postgres_container"],
        database=source["database"],
        database_url=_required_rehearsal_env(source, "database_url_env"),
        object_store_endpoint=source["object_store_endpoint"],
        buckets=dict(source["buckets"]),
        object_store_access_key=_required_rehearsal_env(source, "object_store_access_key_env"),
        object_store_secret_key=_required_rehearsal_env(source, "object_store_secret_key_env"),
        release_store_path=REPO_ROOT / source["release_store_path"],
        keyring_path=REPO_ROOT / source["keyring_path"],
        board=source.get("board", "local:board"),
    )


def _backup(args: argparse.Namespace) -> int:
    from polycodebench_operations.recovery import backup

    _, source = _rehearsal_source(args.config)
    manifest = backup(source, args.out)
    _emit(
        {
            "backup": str(args.out),
            "objects": len(manifest["objects"]),
            "tables": len(manifest["table_row_counts"]),
            "rows": sum(manifest["table_row_counts"].values()),
            "duration_seconds": manifest["duration_seconds"],
        }
    )
    return 0


def _restore_verify(args: argparse.Namespace, registry: MetricsRegistry) -> int:
    import time as _time

    from polycodebench_operations.recovery import (
        RestoreVerificationError,
        StepTimer,
        verify_restored,
    )

    timer = StepTimer()
    started = _time.perf_counter()
    failure = None
    failure_code = None
    try:
        verify_restored(
            timer,
            database_url=args.database_url,
            object_store_endpoint=args.object_store_endpoint,
            buckets={
                "hidden": args.bucket_hidden,
                "internal": args.bucket_internal,
                "public": args.bucket_public,
            },
            release_store_path=args.release_store,
            keyring_document=json.loads(args.keyring.read_text(encoding="utf-8")),
            board=args.board,
            expected_row_counts=None,
            replay_count=args.replay_count,
            seed=args.seed,
        )
    except Exception as error:  # noqa: BLE001 - reported, exit code 1
        failure = type(error).__name__
        if isinstance(error, RestoreVerificationError):
            failure_code = error.safe_code
    report = {
        "passed": failure is None,
        "failure": failure,
        "failure_code": failure_code,
        "verification_seconds": round(_time.perf_counter() - started, 3),
        "steps": timer.steps,
    }
    registry.set(
        "pcb_backup_integrity_failures",
        float(sum(1 for step in timer.steps if step["status"] == "failed")),
        target="verify",
    )
    if args.evidence:
        args.evidence.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _emit(
        {key: report[key] for key in ("passed", "failure", "failure_code", "verification_seconds")}
        | {"steps": [{"step": s["step"], "status": s["status"]} for s in timer.steps]}
    )
    return 0 if failure is None else 1


def _restore(args: argparse.Namespace, registry: MetricsRegistry) -> int:
    if args.action == "verify":
        return _restore_verify(args, registry)
    if args.target == "aws-pitr":
        _emit(
            {
                "target": "aws-pitr",
                "status": "blocked",
                "reason": "no authorized AWS account/region, restore-operator principal or "
                "deployed staging environment exists; see "
                "docs/operations/staging-execution-plan.md",
            }
        )
        return 3
    from polycodebench_operations.recovery import rehearse_restore, summary

    config, _ = _rehearsal_source(args.config)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work_dir = args.work_dir or REPO_ROOT / ".local" / "ops-rehearsal" / f"restore-{stamp}"
    report = rehearse_restore(
        args.backup,
        work_dir=work_dir,
        replay_count=int(config["replay"]["count"]),
        seed=str(config["replay"]["seed"]),
        registry=registry,
    )
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
    _emit(summary(report))
    return 0 if report["passed"] else 1


def _keys(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )
    from polycodebench_publication.keyring import Keyring
    from polycodebench_publication.releases import SigningKey

    keyring = (
        Keyring.from_document(json.loads(args.keyring.read_text(encoding="utf-8")))
        if args.keyring.exists()
        else Keyring()
    )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if args.action == "verify":
        valid, state = keyring.verify(json.loads(args.manifest.read_text(encoding="utf-8")))
        _emit({"valid": valid, "detail": state})
        return 0 if valid else 1
    if args.action == "rotate":
        if args.private_key_out.exists():
            print("refusing to overwrite an existing private key file", file=sys.stderr)
            return 2
        signer = SigningKey(args.new_key_id, Ed25519PrivateKey.generate())
        updated = keyring.rotate(signer, at=now)
        args.private_key_out.parent.mkdir(parents=True, exist_ok=True)
        args.private_key_out.write_bytes(
            signer.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
    else:
        updated = keyring.revoke(args.key_id, at=now, reason=args.reason)
    document = updated.document()
    args.keyring.write_text(json.dumps(document, indent=2), encoding="utf-8")
    _emit(
        {
            "keyring_digest": document["keyring_digest"],
            "keys": [{"key_id": entry.key_id, "state": entry.state} for entry in updated.entries],
        }
    )
    return 0


def _sync_publication(args: argparse.Namespace) -> int:
    from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog
    from polycodebench_publication.keyring import Keyring, KeyringError
    from polycodebench_publication.releases import ReleaseStore

    from polycodebench_operations.release_sync import sync_publication

    database = None
    try:
        keyring = Keyring.from_document(json.loads(args.keyring.read_text(encoding="utf-8")))
        source = ReleaseStore(args.store)
        database = _database()
        result = sync_publication(
            source,
            PostgresPublicReleaseCatalog(database.engine),
            keyring=keyring,
            target=args.target,
            source_target=args.source_target,
        )
    except (KeyringError, InvalidState, ValueError, OSError) as error:
        _emit({"synced": False, "error": type(error).__name__, "reason": str(error)[:300]})
        return 1
    except Exception:
        # Driver diagnostics can include connection details. Keep credentials and SQL out of the
        # operator's terminal output; the structured service log carries the request context.
        _emit({"synced": False, "error": "ReleaseSyncUnavailable"})
        return 1
    finally:
        if database is not None:
            database.dispose()
    _emit({"synced": True, "target": args.target, **result})
    return 0


def _build_live(args: argparse.Namespace) -> int:
    from polycodebench_operations.live_release import build_live_release, write_live_release

    database = None
    try:
        database = _database()
        content, projection, evidence = build_live_release(database.engine, args.run_id)
        paths = write_live_release(args.output_dir, content, projection, evidence)
    except (InvalidState, ValueError, OSError) as error:
        _emit({"built": False, "error": type(error).__name__, "reason": str(error)[:300]})
        return 1
    finally:
        if database is not None:
            database.dispose()
    _emit(
        {
            "built": True,
            "fixture_kind": projection["fixture_kind"],
            "scope": projection["scope"],
            "entries": [entry["model_config_id"] for entry in content["entries"]],
            "not_applicable_checks": sorted(
                row["check"] for row in evidence if row.get("outcome") is not None
            ),
            **paths,
        }
    )
    return 0


def check_alert_rules(path: Path = ALERT_RULES) -> list[str]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = {spec.name for spec in REQUIRED_METRICS}
    problems: list[str] = []
    import re

    for group in document.get("groups", []):
        for rule in group.get("rules", []):
            alert = rule.get("alert", "<recording>")
            referenced = set(re.findall(r"\bpcb_[a-z0-9_]+", rule.get("expr", "")))
            for metric in referenced:
                base = re.sub(r"_(bucket|sum|count)$", "", metric)
                if metric not in names and base not in names:
                    problems.append(f"{alert}: unknown metric {metric}")
            runbook = rule.get("annotations", {}).get("runbook", "")
            if "alert" in rule and not (REPO_ROOT / runbook).is_file():
                problems.append(f"{alert}: runbook {runbook!r} does not exist")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(
        environment=os.environ.get("PCB_ENVIRONMENT", "dev"),
        role=os.environ.get("PCB_ROLE", "operator"),
        log_format="json" if os.environ.get("PCB_LOG_FORMAT", "json") == "json" else "text",
        stream=sys.stderr,
    )
    registry = MetricsRegistry()
    try:
        if args.command == "doctor":
            return _doctor(args.env)
        if args.command == "env":
            if args.action == "validate":
                reports, violations = environments.validate_all()
                _emit(
                    {
                        "environments": [
                            {
                                "environment": r.environment,
                                "status": r.status,
                                "unresolved_inputs": len(r.unresolved_inputs),
                                "deployable": r.deployable,
                            }
                            for r in reports
                        ],
                        "separation_violations": violations,
                    }
                )
                return 1 if violations else 0
            manifest = environments.load_manifest(environments.manifest_path(args.env))
            differences = environments.reconcile(
                manifest, environments.load_terraform_output(args.terraform_output)
            )
            _emit({"environment": args.env, "differences": differences})
            return 1 if differences else 0
        if args.command == "identity":
            return _identity_verify(args)
        if args.command == "migrate":
            return _migrate(args)
        if args.command == "workers":
            return _workers_drain(args.worker_ids)
        if args.command == "orphans":
            return _orphans(args, registry)
        if args.command == "artifacts":
            return _collect_garbage()
        if args.command == "backup":
            return _backup(args)
        if args.command == "restore":
            return _restore(args, registry)
        if args.command == "keys":
            return _keys(args)
        if args.command == "releases" and args.action == "sync-publication":
            return _sync_publication(args)
        if args.command == "releases" and args.action == "build-live":
            return _build_live(args)
        if args.command != "alerts":
            raise AssertionError(f"unhandled command {args.command!r}")
        problems = check_alert_rules()
        _emit({"rules": str(ALERT_RULES.relative_to(REPO_ROOT)), "problems": problems})
        return 1 if problems else 0
    except environments.ManifestError as error:
        print(f"manifest error: {error}", file=sys.stderr)
        return 2
    except DeploymentRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
