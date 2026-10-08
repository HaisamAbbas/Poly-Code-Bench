from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import cast
from uuid import UUID

import polycodebench_orchestration.grading.worker_runtime as grading_runtime_module
import polycodebench_orchestration.worker_cli as worker_cli_module
import pytest
import yaml
from polycodebench_core.canonical import (
    canonical_digest,
    canonical_document_digest,
    canonical_envelope,
)
from polycodebench_core.deployment import EnvironmentManifest, VerifiedDeployment
from polycodebench_orchestration.grading.worker_runtime import (
    GradingWorkerResourceSpec,
    build_ec2_evaluation_worker,
    load_grading_image_allowlist,
    load_grading_resource_for_worker,
)
from polycodebench_orchestration.solve.control_identity import temporary_control_identity
from polycodebench_orchestration.solve.worker_runtime import (
    SolveWorkerResourceSpec,
    build_ec2_solve_worker,
    load_image_allowlist,
)
from polycodebench_orchestration.worker_cli import main as worker_main
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_plugins_api import load_allowlist
from polycodebench_runner.provider import (
    Ec2VmSandboxProvider,
    GuestControlChannel,
    LocalDockerSandboxProvider,
)
from pydantic import ValidationError
from sqlalchemy.engine import Engine

IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"


def _deployed_staging_manifest() -> EnvironmentManifest:
    document = yaml.safe_load(Path("config/environments/staging.yaml").read_text("utf-8"))
    value = json.dumps(document)
    for unresolved, resolved in (
        ("REQUIRED-account", "123456789012"),
        ("REQUIRED-region", "eu-west-1"),
        ("REQUIRED-prefix", "pcbtest"),
        ("ami-REQUIRED", "ami-0abc"),
        ("REQUIRED-guest-instance-type", "m7i.large"),
        ("lt-REQUIRED", "lt-0abc"),
        ("subnet-REQUIRED", "subnet-0abc"),
        ("sg-REQUIRED", "sg-0abc"),
    ):
        value = value.replace(unresolved, resolved)
    result = json.loads(value)
    result["sandbox"]["lane_subnets"] = {
        "solve": "subnet-0a01",
        "grading": "subnet-0a02",
        "admission": "subnet-0a03",
    }
    result["sandbox"]["lane_security_groups"] = {
        "solve": "sg-0a01",
        "grading": "sg-0a02",
        "admission": "sg-0a03",
    }
    result["sandbox"]["control_security_group_id"] = "sg-0a04"
    result["sandbox"]["launch_templates"] = {
        "solve": "lt-0a01",
        "grading": "lt-0a02",
        "admission": "lt-0a03",
        "performance": "lt-0a04",
    }
    result["sandbox"]["launch_template_versions"] = {
        "solve": "4",
        "grading": "7",
        "admission": "3",
        "performance": "2",
    }
    result["status"] = "deployed"
    return EnvironmentManifest.model_validate(result)


def _resource_spec(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": "resource_spec",
        "resource_class": "local-fixture-small",
        "lane": "solve",
        "image": IMAGE,
        "image_digest": DIGEST,
        "cpu_millis": 1000,
        "memory_bytes": 512 * 1024**2,
        "disk_bytes": 128 * 1024**2,
        "pids_limit": 128,
        "timeout_seconds": 600,
        "ttl_seconds": 900,
        "executable_workspace": False,
    }
    document.update(changes)
    return document


def test_worker_resource_spec_binds_class_limits_and_image_digest() -> None:
    resource = SolveWorkerResourceSpec.model_validate(_resource_spec(), strict=True)
    assert resource.resource_class == "local-fixture-small"
    assert resource.image_digest == DIGEST
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(
            _resource_spec(image_digest="sha256:" + "0" * 64), strict=True
        )
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(
            _resource_spec(image=IMAGE.replace("@sha256:", "@sha256:untrusted")), strict=True
        )
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(_resource_spec(disk_bytes=512 * 1024**2 + 1))


def test_local_image_allowlist_rejects_floating_or_mismatched_images(tmp_path: Path) -> None:
    assert load_image_allowlist(Path("config/worker/local-image-allowlist.json")) == {IMAGE: DIGEST}
    invalid = tmp_path / "images.json"
    invalid.write_text(json.dumps({"python:latest": DIGEST}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)
    invalid.write_text(json.dumps({IMAGE: "sha256:" + "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)
    malformed = IMAGE.replace("@sha256:", "@sha256:untrusted")
    invalid.write_text(json.dumps({malformed: DIGEST}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)


def test_grading_image_allowlist_comes_from_versioned_language_identities() -> None:
    images = load_grading_image_allowlist(Path("config/images"))

    assert len(images) >= 20
    assert all(reference.endswith("@" + digest) for reference, digest in images.items())
    assert any("python-evaluator" in reference for reference in images)
    assert any("javascript-evaluator" in reference for reference in images)


def test_ec2_worker_assembly_requires_verified_solve_role_and_uses_manifest_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_VERIFIED_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_VERIFIED_ROLE", "solve-supervisor")
    monkeypatch.setenv("PCB_VERIFIED_BY", "aws-sts")
    manifest = _deployed_staging_manifest()
    deployment = VerifiedDeployment(
        environment="staging",
        role="solve-supervisor",
        isolation_tier="production",
        principal=(
            "arn:aws:sts::123456789012:assumed-role/pcb-staging-solve-supervisor/ecs-task-9"
        ),
        verified_by="aws-sts",
        ranked_release_allowed=False,
    )

    class FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            raise AssertionError("identity is checked at guest creation, not assembly")

    worker = build_ec2_solve_worker(
        worker_id=UUID("00000000-0000-0000-0000-000000000001"),
        manifest=manifest,
        deployment=deployment,
        scheduler_engine=cast(Engine, object()),
        solve_engine=cast(Engine, object()),
        gateway_engine=cast(Engine, object()),
        artifact_engine=cast(Engine, object()),
        object_store=cast(S3ArtifactStore, object()),
        candidate_image_digests={IMAGE: DIGEST},
        protocol_directory=Path("config/protocols"),
        budget_profile_file=Path("config/budgets/pilot-v1.yaml"),
        ec2_client=object(),
        sts_client=FakeSts(),
        secretsmanager_client=object(),
        control_channel=cast(GuestControlChannel, object()),
    )
    assert isinstance(worker.sandbox, Ec2VmSandboxProvider)
    assert worker.sandbox.approved_ami_id == "ami-0abc"
    assert worker.sandbox.launch_template_by_lane == {
        "solve": "lt-0a01",
        "grading": "lt-0a02",
        "admission": "lt-0a03",
    }

    scheduler_deployment = VerifiedDeployment(
        environment="staging",
        role="scheduler",
        isolation_tier="production",
        principal="arn:aws:sts::123456789012:assumed-role/pcb-staging-scheduler/ecs-task-9",
        verified_by="aws-sts",
        ranked_release_allowed=False,
    )
    with pytest.raises(ValueError, match="requires the solve-supervisor role"):
        build_ec2_solve_worker(
            worker_id=UUID("00000000-0000-0000-0000-000000000001"),
            manifest=manifest,
            deployment=scheduler_deployment,
            scheduler_engine=cast(Engine, object()),
            solve_engine=cast(Engine, object()),
            gateway_engine=cast(Engine, object()),
            artifact_engine=cast(Engine, object()),
            object_store=cast(S3ArtifactStore, object()),
            candidate_image_digests={IMAGE: DIGEST},
            protocol_directory=Path("config/protocols"),
            budget_profile_file=Path("config/budgets/pilot-v1.yaml"),
            ec2_client=object(),
            sts_client=FakeSts(),
            secretsmanager_client=object(),
            control_channel=cast(GuestControlChannel, object()),
        )


def test_ec2_evaluator_assembly_is_bound_to_eval_role_and_production_guests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _deployed_staging_manifest()
    deployment = VerifiedDeployment(
        environment="staging",
        role="eval-supervisor",
        isolation_tier="production",
        principal=(
            "arn:aws:sts::123456789012:assumed-role/pcb-staging-eval-supervisor/ecs-task-9"
        ),
        verified_by="aws-sts",
        ranked_release_allowed=False,
    )
    plugins = load_allowlist(Path("config/plugins/allowlist-v1.yaml"))
    resource = GradingWorkerResourceSpec(
        schema_version=1,
        kind="resource_spec",
        resource_class="aws-grading-small",
        lane="grading",
        plugin_allowlist_digest=canonical_document_digest(plugins),
        image_allowlist_digest=canonical_digest(
            load_grading_image_allowlist(Path("config/images"))
        ),
    )
    monkeypatch.setattr(
        grading_runtime_module,
        "load_grading_resource_for_worker",
        lambda *_args, **_kwargs: resource,
    )

    class FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            raise AssertionError("identity is rechecked at guest creation, not assembly")

    worker = build_ec2_evaluation_worker(
        worker_id=UUID("00000000-0000-0000-0000-000000000002"),
        manifest=manifest,
        deployment=deployment,
        scheduler_engine=cast(Engine, object()),
        evaluator_engine=cast(Engine, object()),
        artifact_engine=cast(Engine, object()),
        object_store=cast(S3ArtifactStore, object()),
        plugin_allowlist_path=Path("config/plugins/allowlist-v1.yaml"),
        image_identity_directory=Path("config/images"),
        ec2_client=object(),
        sts_client=FakeSts(),
        control_channel=cast(GuestControlChannel, object()),
    )
    assert isinstance(worker.sandbox, Ec2VmSandboxProvider)
    assert worker.sandbox.supervisor_role == "eval-supervisor"
    assert worker.sandbox.subnet_by_lane["grading"] == "subnet-0a02"
    assert worker.executor._tier == "production_worker"

    with pytest.raises(ValueError, match="does not bind language image identities"):
        grading_runtime_module._build_evaluation_worker(
            worker_id=UUID("00000000-0000-0000-0000-000000000002"),
            scheduler_engine=cast(Engine, object()),
            evaluator_engine=cast(Engine, object()),
            artifact_engine=cast(Engine, object()),
            object_store=cast(S3ArtifactStore, object()),
            plugin_allowlist_path=Path("config/plugins/allowlist-v1.yaml"),
            resource=resource.model_copy(update={"image_allowlist_digest": None}),
            images=load_grading_image_allowlist(Path("config/images")),
            sandbox=worker.sandbox,
            execution_tier="production_worker",
        )
    with pytest.raises(ValueError, match="language images differ from its registered resource"):
        grading_runtime_module._build_evaluation_worker(
            worker_id=UUID("00000000-0000-0000-0000-000000000002"),
            scheduler_engine=cast(Engine, object()),
            evaluator_engine=cast(Engine, object()),
            artifact_engine=cast(Engine, object()),
            object_store=cast(S3ArtifactStore, object()),
            plugin_allowlist_path=Path("config/plugins/allowlist-v1.yaml"),
            resource=resource.model_copy(update={"image_allowlist_digest": "sha256:" + "0" * 64}),
            images=load_grading_image_allowlist(Path("config/images")),
            sandbox=worker.sandbox,
            execution_tier="production_worker",
        )

    wrong_role = VerifiedDeployment(
        environment="staging",
        role="solve-supervisor",
        isolation_tier="production",
        principal=(
            "arn:aws:sts::123456789012:assumed-role/pcb-staging-solve-supervisor/ecs-task-9"
        ),
        verified_by="aws-sts",
        ranked_release_allowed=False,
    )
    with pytest.raises(ValueError, match="requires the eval-supervisor role"):
        build_ec2_evaluation_worker(
            worker_id=UUID("00000000-0000-0000-0000-000000000002"),
            manifest=manifest,
            deployment=wrong_role,
            scheduler_engine=cast(Engine, object()),
            evaluator_engine=cast(Engine, object()),
            artifact_engine=cast(Engine, object()),
            object_store=cast(S3ArtifactStore, object()),
            plugin_allowlist_path=Path("config/plugins/allowlist-v1.yaml"),
            image_identity_directory=Path("config/images"),
            ec2_client=object(),
            sts_client=FakeSts(),
            control_channel=cast(GuestControlChannel, object()),
        )


def test_ec2_evaluator_requires_matching_registered_grading_driver() -> None:
    plugins = load_allowlist(Path("config/plugins/allowlist-v1.yaml"))
    resource = GradingWorkerResourceSpec(
        schema_version=1,
        kind="resource_spec",
        resource_class="aws-grading-small",
        lane="grading",
        plugin_allowlist_digest=canonical_document_digest(plugins),
    )
    legacy_document = resource.model_dump(mode="json", exclude={"image_allowlist_digest"})
    legacy_payload = {
        key: value
        for key, value in legacy_document.items()
        if key not in {"kind", "schema_version"}
    }
    digest = canonical_digest(canonical_envelope("resource_spec", legacy_payload, 1))
    row = {
        "worker_status": "active",
        "lane": "grading",
        "hardware_class": "aws-ec2-disposable-vm-v1",
        "driver_identity": "Ec2VmSandboxProvider",
        "slot_resource_class": resource.resource_class,
        "document": legacy_document,
        "digest": digest,
        "artifact_status": "verified",
        "visibility": "internal",
        "encryption_domain": "worker-config",
        "content_digest": digest,
    }

    class Rows:
        def mappings(self) -> Rows:
            return self

        def all(self) -> list[dict[str, object]]:
            return [row]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _query: object) -> Rows:
            return Rows()

    class EngineDouble:
        def connect(self) -> Connection:
            return Connection()

    worker_id = UUID("00000000-0000-0000-0000-000000000003")
    loaded = load_grading_resource_for_worker(
        cast(Engine, EngineDouble()),
        worker_id,
        expected_driver_identity="Ec2VmSandboxProvider",
    )
    assert loaded == resource

    row["driver_identity"] = "LocalDockerSandboxProvider"
    assert load_grading_resource_for_worker(cast(Engine, EngineDouble()), worker_id) == resource
    with pytest.raises(ValueError, match="does not bind verified configuration"):
        load_grading_resource_for_worker(
            cast(Engine, EngineDouble()),
            worker_id,
            expected_driver_identity="Ec2VmSandboxProvider",
        )

    row["driver_identity"] = "Ec2VmSandboxProvider"
    current = resource.model_copy(update={"image_allowlist_digest": "sha256:" + "1" * 64})
    current_document = current.model_dump(mode="json")
    current_digest = canonical_document_digest(current)
    row["document"] = current_document
    row["digest"] = current_digest
    row["content_digest"] = current_digest
    assert (
        load_grading_resource_for_worker(
            cast(Engine, EngineDouble()),
            worker_id,
            expected_driver_identity="Ec2VmSandboxProvider",
        )
        == current
    )


def test_ec2_grading_registration_locks_capacity_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, rows: list[dict[str, object]], count: int = 0) -> None:
            self.rows = rows
            self.count = count

        def mappings(self) -> Result:
            return self

        def all(self) -> list[dict[str, object]]:
            return self.rows

        def scalar_one(self) -> int:
            return self.count

    class Connection:
        def __init__(self, rows: list[dict[str, object]], count: int) -> None:
            self.result = Result(rows, count)
            self.queries: list[str] = []

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: object, _params: object = None) -> Result:
            self.queries.append(str(statement))
            return self.result

    class EngineDouble:
        def __init__(self, rows: list[dict[str, object]] | None = None, count: int = 0) -> None:
            self.connection = Connection(rows or [], count)

        def begin(self) -> Connection:
            return self.connection

    registered: list[tuple[object, str | None]] = []

    class JobRepositoryDouble:
        def __init__(self, _engine: object) -> None:
            pass

        def register_worker(self, spec: object, *, actor_subject: str | None = None) -> UUID:
            registered.append((spec, actor_subject))
            return UUID("00000000-0000-0000-0000-000000000005")

    monkeypatch.setattr(worker_cli_module, "PostgresJobRepository", JobRepositoryDouble)
    args = {
        "workload_identity": "staging-grading-aws-grading-small",
        "hardware_class": "aws-ec2-disposable-vm-v1",
        "driver_identity": "Ec2VmSandboxProvider",
        "resource_class": "aws-grading-small",
        "resource_config_id": UUID("10000000-0000-0000-0000-000000000001"),
        "resource_digest": "sha256:" + "2" * 64,
        "slots": 1,
        "actor_identity": "arn:aws:sts::123456789012:assumed-role/pcb-staging-migrator/task",
        "environment": "staging",
        "max_concurrency": 1,
    }
    engine = EngineDouble()
    worker_id, created = worker_cli_module._register_grading_capacity(
        engine=cast(Engine, engine), **args
    )
    assert worker_id == UUID("00000000-0000-0000-0000-000000000005")
    assert created is True
    assert "pg_advisory_xact_lock" in engine.connection.queries[0]
    assert registered[0][1] == args["actor_identity"]

    existing = {
        "id": UUID("00000000-0000-0000-0000-000000000006"),
        "status": "active",
        "lane": "grading",
        "hardware_class": args["hardware_class"],
        "driver_identity": args["driver_identity"],
        "allowed_queue_classes": ["grading"],
        "allowed_resource_classes": [args["resource_class"]],
        "slot_key": "grading-slot-0",
        "resource_class": args["resource_class"],
        "digest": args["resource_digest"],
    }
    replay_id, replay_created = worker_cli_module._register_grading_capacity(
        engine=cast(Engine, EngineDouble([existing])), **args
    )
    assert replay_id == existing["id"]
    assert replay_created is False
    assert len(registered) == 1

    with pytest.raises(ValueError, match="exceeds the environment capacity cap"):
        worker_cli_module._register_grading_capacity(
            engine=cast(Engine, EngineDouble(count=1)), **args
        )
    assert len(registered) == 1


def test_local_evaluator_assembly_keeps_development_tier(tmp_path: Path) -> None:
    plugins = load_allowlist(Path("config/plugins/allowlist-v1.yaml"))
    resource = GradingWorkerResourceSpec(
        schema_version=1,
        kind="resource_spec",
        resource_class="local-grading-small",
        lane="grading",
        plugin_allowlist_digest=canonical_document_digest(plugins),
    )
    worker = grading_runtime_module.build_local_evaluation_worker(
        worker_id=UUID("00000000-0000-0000-0000-000000000004"),
        scheduler_engine=cast(Engine, object()),
        evaluator_engine=cast(Engine, object()),
        artifact_engine=cast(Engine, object()),
        object_store=cast(S3ArtifactStore, object()),
        plugin_allowlist_path=Path("config/plugins/allowlist-v1.yaml"),
        image_identity_directory=Path("config/images"),
        state_dir=tmp_path,
        resource=resource,
    )

    assert isinstance(worker.sandbox, LocalDockerSandboxProvider)
    assert worker.executor._tier == "development_sandbox"


def test_guest_control_identity_is_role_scoped_temporary_and_private() -> None:
    manifest = _deployed_staging_manifest()
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "test-only-private-bytes\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )

    class FakeSecretsManager:
        requested_id: str | None = None

        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            self.requested_id = SecretId
            return {"SecretString": key}

    client = FakeSecretsManager()
    with temporary_control_identity(
        secrets_manager_client=client,
        manifest=manifest,
        supervisor_role="solve-supervisor",
    ) as identity_file:
        assert identity_file.is_file()
        assert identity_file.read_text("ascii") == key
        if os.name == "posix":
            assert stat.S_IMODE(identity_file.stat().st_mode) == 0o600
        assert client.requested_id == ("pcb/staging/sandbox/control-identity-solve-supervisor")
    assert not identity_file.exists()

    with temporary_control_identity(
        secrets_manager_client=client,
        manifest=manifest,
        supervisor_role="eval-supervisor",
    ) as identity_file:
        assert identity_file.read_text("ascii") == key
        assert client.requested_id == "pcb/staging/sandbox/control-identity-eval-supervisor"
    assert not identity_file.exists()

    mismatched_sandbox = manifest.sandbox.model_copy(
        update={
            "control_identity_secret_refs": {
                **manifest.sandbox.control_identity_secret_refs,
                "eval-supervisor": manifest.sandbox.control_identity_secret_refs[
                    "solve-supervisor"
                ],
            }
        }
    )
    mismatched_manifest = manifest.model_copy(update={"sandbox": mismatched_sandbox})
    with pytest.raises(ValueError, match="outside this role"):
        with temporary_control_identity(
            secrets_manager_client=client,
            manifest=mismatched_manifest,
            supervisor_role="eval-supervisor",
        ):
            pytest.fail("a solve key must not be selected as the evaluation role's key")


def test_worker_commands_are_inert_without_the_local_opt_in_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.delenv("PCB_LOCAL_WORKER_SETUP_ENABLED", raising=False)
    monkeypatch.delenv("PCB_LOCAL_GRADING_SCHEDULE_ENABLED", raising=False)
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)

    assert worker_main(["local-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    job_id = UUID("22222222-2222-4222-8222-222222222222")
    assert worker_main(["local-run", "--job-id", str(job_id)]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err

    worker_id = UUID("33333333-3333-4333-8333-333333333333")
    evaluation_job_id = UUID("44444444-4444-4444-8444-444444444444")
    assert worker_main(["local-grading-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    assert worker_main(["ec2-grading-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    attempt_id = UUID("55555555-5555-4555-8555-555555555555")
    policy_id = UUID("66666666-6666-4666-8666-666666666666")
    assert worker_main(
        [
            "local-grading-enqueue",
            "--attempt-id",
            str(attempt_id),
            "--policy-config-id",
            str(policy_id),
        ]
    ) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    assert worker_main(
        ["ec2-grading-run", "--watch", "--known-hosts", "missing-known-hosts"]
    ) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    assert worker_main(
        [
            "local-grading-run",
            "--worker-id",
            str(worker_id),
            "--job-id",
            str(evaluation_job_id),
        ]
    ) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err


def test_exact_local_job_cannot_be_combined_with_watch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.setenv("PCB_WORKER_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "test-local-worker")
    monkeypatch.setattr(
        worker_cli_module,
        "_worker_database",
        lambda: pytest.fail("invalid one-shot filters must fail before database access"),
    )
    job_id = UUID("11111111-1111-4111-8111-111111111111")

    assert worker_main(["local-run", "--watch", "--job-id", str(job_id)]) == 2
    assert "--job-id cannot be combined with --watch" in capsys.readouterr().err


def test_local_one_shot_requires_exact_job_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.setenv("PCB_WORKER_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "test-local-worker")

    database_accesses: list[bool] = []

    def unexpected_database_access() -> None:
        database_accesses.append(True)
        raise RuntimeError("unfiltered one-shot reached the database")

    monkeypatch.setattr(
        worker_cli_module,
        "_worker_database",
        unexpected_database_access,
    )

    assert worker_main(["local-run"]) == 2
    assert database_accesses == []
    assert "local one-shot requires --job-id or --run-id" in capsys.readouterr().err


def test_local_run_id_can_bound_watch_without_opening_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)

    database_accesses: list[bool] = []

    def unexpected_database_access() -> None:
        database_accesses.append(True)
        raise RuntimeError("dispatch opt-in must be checked before database access")

    monkeypatch.setattr(
        worker_cli_module,
        "_worker_database",
        unexpected_database_access,
    )
    run_id = UUID("33333333-3333-4333-8333-333333333333")

    assert worker_main(["local-run", "--watch", "--run-id", str(run_id)]) == 2
    output = capsys.readouterr().err
    assert database_accesses == []
    assert "worker configuration or operation failed" in output
    assert "local one-shot requires" not in output
    assert "cannot be combined with --watch" not in output


def test_local_worker_rejects_two_target_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        worker_cli_module._parser().parse_args(
            [
                "local-run",
                "--job-id",
                "11111111-1111-4111-8111-111111111111",
                "--run-id",
                "22222222-2222-4222-8222-222222222222",
            ]
        )
    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_ec2_worker_command_requires_an_explicit_dispatch_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)
    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("AWS clients must not be constructed before opt-in"),
    )
    assert (
        worker_main(
            [
                "ec2-run",
                "--image-allowlist",
                "config/worker/local-image-allowlist.json",
                "--known-hosts",
                "does-not-exist",
            ]
        )
        == 2
    )
    assert "worker configuration or operation failed" in capsys.readouterr().err


def test_ec2_evaluator_command_requires_dispatch_opt_in_before_aws_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)
    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("AWS clients must not be constructed before opt-in"),
    )

    assert (
        worker_main(
            ["ec2-grading-run", "--watch", "--known-hosts", "does-not-exist"]
        )
        == 2
    )
    assert "worker configuration or operation failed" in capsys.readouterr().err


def test_ec2_registration_command_requires_explicit_setup_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.delenv("PCB_AWS_WORKER_SETUP_ENABLED", raising=False)
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)
    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("AWS clients must not be constructed before opt-in"),
    )
    assert (
        worker_main(
            [
                "ec2-register",
                "--resource-spec",
                "config/worker/local-small-resource.json",
                "--image-allowlist",
                "config/worker/local-image-allowlist.json",
            ]
        )
        == 2
    )
    assert "worker configuration or operation failed" in capsys.readouterr().err


def test_ec2_evaluation_registration_refuses_to_run_with_dispatch_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_AWS_WORKER_SETUP_ENABLED", "true")
    monkeypatch.setenv("PCB_WORKER_DISPATCH_ENABLED", "true")
    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda *args, **kwargs: pytest.fail("registration must fail before reading AWS identity"),
    )

    assert worker_main(["ec2-grading-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err


def test_aws_worker_manifest_checks_live_sts_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _deployed_staging_manifest()
    manifest_path = tmp_path / "staging.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest.model_dump(mode="json")), encoding="utf-8")
    principal = "arn:aws:sts::123456789012:assumed-role/pcb-staging-migrator/ecs-task-7"
    monkeypatch.setenv("PCB_ENV_MANIFEST", str(manifest_path))
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_VERIFIED_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_VERIFIED_ROLE", "migrator")
    monkeypatch.setenv("PCB_VERIFIED_BY", "aws-sts")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", principal)

    class FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": "123456789012", "Arn": principal}

    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda service, **kwargs: (
            FakeSts()
            if service == "sts" and kwargs["region_name"] == "eu-west-1"
            else pytest.fail("only regional STS identity verification is expected")
        ),
    )
    verified_manifest, deployment = worker_cli_module._read_aws_manifest("migrator")
    assert verified_manifest.environment == "staging"
    assert deployment.role == "migrator"
    assert deployment.principal == principal

    monkeypatch.setattr(
        worker_cli_module.boto3,
        "client",
        lambda *args, **kwargs: type(
            "SpoofedSts",
            (),
            {
                "get_caller_identity": lambda self: {
                    "Account": "123456789012",
                    "Arn": (
                        "arn:aws:sts::123456789012:assumed-role/"
                        "pcb-staging-solve-supervisor/ecs-task-8"
                    ),
                }
            },
        )(),
    )
    with pytest.raises(ValueError, match="principal does not match"):
        worker_cli_module._read_aws_manifest("migrator")
