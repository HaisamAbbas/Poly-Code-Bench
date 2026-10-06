from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import cast
from uuid import UUID

import polycodebench_orchestration.worker_cli as worker_cli_module
import pytest
import yaml
from polycodebench_core.deployment import EnvironmentManifest, VerifiedDeployment
from polycodebench_orchestration.solve.control_identity import temporary_control_identity
from polycodebench_orchestration.solve.worker_runtime import (
    SolveWorkerResourceSpec,
    build_ec2_solve_worker,
    load_image_allowlist,
)
from polycodebench_orchestration.worker_cli import main as worker_main
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_runner.provider import Ec2VmSandboxProvider, GuestControlChannel
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
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)

    assert worker_main(["local-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    assert worker_main(["local-run"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err


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
