"""Sandbox contract checks that do not claim a live Docker/VM boundary."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from polycodebench_runner.contracts import ExecRequest, InputManifest, SandboxHandle, SandboxSpec
from polycodebench_runner.provider import (
    AwsWorkerIdentityVerifier,
    Ec2VmSandboxProvider,
    LocalDockerSandboxProvider,
    SandboxError,
    run_bounded_process,
)

IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"


def _spec(**overrides: object) -> SandboxSpec:
    values: dict[str, object] = dict(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        image=IMAGE,
        image_digest=DIGEST,
        cpu_millis=1000,
        memory_bytes=256 * 1024**2,
        disk_bytes=64 * 1024**2,
        pids_limit=32,
        timeout_seconds=10,
        ttl_seconds=300,
    )
    values.update(overrides)
    return SandboxSpec(**values)  # type: ignore[arg-type]


def _provider() -> LocalDockerSandboxProvider:
    state_dir = Path.cwd() / ".cache" / f"prompt06-state-{uuid4().hex}"
    return LocalDockerSandboxProvider(
        allowed_images={IMAGE: DIGEST}, state_dir=state_dir, provider_id="tests"
    )


def test_sandbox_spec_rejects_unpinned_image_and_coercions() -> None:
    with pytest.raises(ValueError, match="pinned"):
        _spec(image="python:latest")
    with pytest.raises(ValueError):
        _spec(cpu_millis="1000")
    with pytest.raises(ValueError):
        ExecRequest.model_validate(
            {"argv": ("python", "x.py"), "timeout_seconds": 1, "extra": "forbidden"}
        )
    with pytest.raises(ValueError, match="protected"):
        ExecRequest(argv=("python",), timeout_seconds=1, environment={"PATH": "/tmp"})
    with pytest.raises(ValueError, match="cannot be requested"):
        SandboxHandle(
            stage_id="stage-test",
            fence=1,
            lane="solve",
            driver="local_docker",
            resource_id="candidate",
            expires_at_epoch=1,
            max_execution_seconds=1,
            isolation_tier="production",
        )


def test_sandbox_policy_documents_keep_development_and_production_tiers_separate() -> None:
    development = json.loads(
        (Path.cwd() / "config/sandbox-policies/development.v1.json").read_text(encoding="utf-8")
    )
    production = json.loads(
        (Path.cwd() / "config/sandbox-policies/production-ec2.v1.json").read_text(encoding="utf-8")
    )
    assert development["driver"] == "local_docker"
    assert development["isolation_tier"] == "development"
    assert development["network"] == "none"
    assert development["docker_socket_mount"] is False
    assert development["host_namespace_sharing"] is False
    assert development["production_attestation_allowed"] is False
    assert production["driver"] == "ec2_vm"
    assert production["isolation_tier"] == "production"
    assert production["instance_profile"] is None
    assert production["metadata_endpoint"] == "disabled"
    assert production["guest_egress"] == "none"
    assert production["solve_grading_lanes_distinct"] is True
    assert production["candidate_image_pull"] == "never"


def test_input_manifest_rejects_unsafe_path_and_unbounded_bytes() -> None:
    with pytest.raises(ValueError):
        InputManifest(files={"../escape": b"x"}, max_total_bytes=1)
    with pytest.raises(ValueError, match="byte bound"):
        InputManifest(files={"main.py": b"xx"}, max_total_bytes=1)
    with pytest.raises(ValueError):
        _spec(isolation_tier="production")


def test_real_host_process_timeout_and_output_retention_limits() -> None:
    timeout = run_bounded_process(
        ["python", "-c", "import time; time.sleep(5)"], timeout=1, output_limit=128
    )
    assert timeout == (None, b"", b"", True)
    flood = run_bounded_process(
        ["python", "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        timeout=5,
        output_limit=128,
    )
    assert flood[0] == 0
    assert len(flood[1]) == 128
    assert flood[3] is False


def test_create_uses_restricted_docker_flags_and_attestation_is_development_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider()
    calls: list[list[str]] = []
    labels: dict[str, str] = {}

    def docker(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        calls.append(args)
        if args[0] == "create":
            for index, value in enumerate(args):
                if value == "--label":
                    key, label_value = args[index + 1].split("=", 1)
                    labels[key] = label_value
            return b"container-id\n"
        if args[0] == "inspect":
            return json.dumps(labels).encode()
        return b""

    monkeypatch.setattr(provider, "_docker", docker)
    handle = asyncio.run(provider.create(_spec()))
    create = calls[0]
    assert "--network=none" in create
    assert "--read-only" in create
    assert "--cap-drop=ALL" in create
    assert "--security-opt=no-new-privileges:true" in create
    assert "--pids-limit" in create
    assert "--memory" in create
    assert create[create.index("--memory") + 1] == create[create.index("--memory-swap") + 1]
    assert "--cpus" in create
    assert "--tmpfs" in create
    assert "--privileged" not in create
    assert not any("docker.sock" in arg or "/var/run" in arg for arg in create)
    assert handle.isolation_tier == "development"
    assert provider.attestation(handle).isolation_tier == "development"


def test_exec_argv_is_passed_as_distinct_host_process_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider()
    # Avoid creating resources: exercise only the host process argument boundary.
    monkeypatch.setattr(provider, "_assert_owned", lambda scoped: None)
    handle_obj = SandboxHandle(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        driver="local_docker",
        resource_id="cid",
        expires_at_epoch=9999999999,
        max_execution_seconds=10,
        isolation_tier="development",
    )
    seen: list[list[str]] = []

    def invoke(
        command: list[str], timeout: int, output_limit: int
    ) -> tuple[int, bytes, bytes, bool]:
        seen.append(command)
        assert timeout == 2
        assert output_limit == 1_048_576
        return 0, b"ok", b"", False

    monkeypatch.setattr("polycodebench_runner.provider.run_bounded_process", invoke)
    request = ExecRequest(argv=("python", "-c", "print('x'); touch owned"), timeout_seconds=2)
    result = provider._execute(handle_obj, request)
    assert result.exit_code == 0
    assert seen[0][-3:] == ["python", "-c", "print('x'); touch owned"]
    assert "shell" not in seen[0]


def test_cancelling_execute_stops_the_scoped_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    handle = SandboxHandle(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        driver="local_docker",
        resource_id="cid",
        expires_at_epoch=9999999999,
        max_execution_seconds=10,
        isolation_tier="development",
    )
    started = threading.Event()
    release = threading.Event()
    stopped: list[str] = []

    def slow_execute(scoped: SandboxHandle, request: ExecRequest) -> None:
        started.set()
        release.wait(timeout=5)
        raise SandboxError("cancelled candidate command stopped")

    def terminate(scoped: SandboxHandle, reason: str) -> None:
        stopped.append(reason)
        release.set()

    monkeypatch.setattr(provider, "_execute", slow_execute)
    monkeypatch.setattr(provider, "_terminate", terminate)

    async def run_cancelled() -> None:
        task = asyncio.create_task(
            provider.execute(handle, ExecRequest(argv=("python",), timeout_seconds=10))
        )
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_cancelled())
    assert stopped == ["caller_cancelled"]


def test_local_expiry_sweeper_honors_epoch_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    handle = SandboxHandle(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        driver="local_docker",
        resource_id="cid-expired",
        expires_at_epoch=1,
        max_execution_seconds=10,
        isolation_tier="development",
    )
    labels = {
        "pcb.provider": provider.provider_id,
        "pcb.sandbox": handle.sandbox_id,
        "pcb.stage": handle.stage_id,
        "pcb.fence": str(handle.fence),
        "pcb.lane": handle.lane,
        "pcb.timeout": str(handle.max_execution_seconds),
        "pcb.expires": str(handle.expires_at_epoch),
    }
    destroyed: list[SandboxHandle] = []

    def docker(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        del input_bytes
        if args[0] == "ps":
            return b"cid-expired\n"
        if args[0] == "inspect":
            return json.dumps(labels).encode()
        return b""

    monkeypatch.setattr(provider, "_docker", docker)
    monkeypatch.setattr(provider, "_destroy", destroyed.append)
    assert asyncio.run(provider.collect_expired(now_epoch=0)) == ()
    assert destroyed == []


def test_snapshot_rejects_symlink_members(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    handle = SandboxHandle(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        driver="local_docker",
        resource_id="cid",
        expires_at_epoch=9999999999,
        max_execution_seconds=10,
        isolation_tier="development",
    )
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../host-secret"
        archive.addfile(member)
    monkeypatch.setattr(provider, "_assert_owned", lambda scoped: None)
    monkeypatch.setattr(provider, "_docker", lambda *args, **kwargs: payload.getvalue())
    with pytest.raises(SandboxError, match="link or special"):
        provider._snapshot(handle)


def test_snapshot_rejects_parent_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    handle = SandboxHandle(
        stage_id="stage-test",
        fence=3,
        lane="admission",
        driver="local_docker",
        resource_id="cid",
        expires_at_epoch=9999999999,
        max_execution_seconds=10,
        isolation_tier="development",
    )
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        member = tarfile.TarInfo("../secret")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    monkeypatch.setattr(provider, "_assert_owned", lambda scoped: None)
    monkeypatch.setattr(provider, "_docker", lambda *args, **kwargs: payload.getvalue())
    with pytest.raises(SandboxError, match="unsafe path|link or special"):
        provider._snapshot(handle)


def test_ec2_driver_uses_verified_identity_scoped_capability_and_private_instance() -> None:
    class FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Arn": "arn:aws:iam::123456789012:role/pcb-sandbox-supervisor"}

    class FakeEc2:
        call: dict[str, object] = {}
        state = "running"

        def run_instances(self, **kwargs: object) -> dict[str, object]:
            self.call = kwargs
            return {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}

        def describe_instances(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            tags = self.call["TagSpecifications"][0]["Tags"]  # type: ignore[index]
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-0123456789abcdef0",
                                "PrivateIpAddress": "10.0.2.14",
                                "State": {"Name": self.state},
                                "Tags": tags,
                                "MetadataOptions": {"HttpEndpoint": "disabled"},
                                "IamInstanceProfile": None,
                                "PublicIpAddress": None,
                                "SubnetId": "subnet-c",
                                "VpcId": "vpc-test",
                                "ImageId": "ami-0123456789abcdef0",
                                "NetworkInterfaces": [
                                    {"Groups": [{"GroupId": "sg-c"}], "Association": {}}
                                ],
                            }
                        ]
                    }
                ]
            }

        def terminate_instances(self, **kwargs: object) -> None:
            del kwargs
            self.state = "terminated"

        def describe_security_groups(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return {
                "SecurityGroups": [
                    {
                        "VpcId": "vpc-test",
                        "IpPermissions": [
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 22,
                                "ToPort": 22,
                                "UserIdGroupPairs": [{"GroupId": "sg-supervisor"}],
                            }
                        ],
                        "IpPermissionsEgress": [],
                    }
                ]
            }

    class FakeChannel:
        calls: list[tuple[str, dict[str, object]]] = []

        def invoke(
            self, private_ip: str, operation: str, payload: dict[str, object]
        ) -> dict[str, object]:
            assert private_ip == "10.0.2.14"
            self.calls.append((operation, payload))
            if operation == "execute":
                return {
                    "ok": True,
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout_b64": base64.b64encode(b"ok").decode(),
                    "stderr_b64": "",
                }
            if operation == "snapshot":
                return {
                    "ok": True,
                    "files": [],
                    "archive_b64": "",
                    "archive_digest": f"sha256:{hashlib.sha256(b'').hexdigest()}",
                }
            if operation == "attest":
                return {
                    "ok": True,
                    "controls": [
                        "network_none",
                        "private_namespaces",
                        "readonly_root",
                        "drop_all_capabilities",
                        "no_new_privileges",
                        "nonroot",
                        "bounded_memory",
                        "bounded_cpu",
                        "bounded_disk",
                        "bounded_pids",
                        "no_mounts_or_socket",
                        "fresh_stage_fence",
                    ],
                }
            return {"ok": True}

    ec2 = FakeEc2()
    channel = FakeChannel()
    verifier = AwsWorkerIdentityVerifier(
        sts_client=FakeSts(),
        expected_supervisor_arn="arn:aws:iam::123456789012:role/pcb-sandbox-supervisor",
    )
    provider = Ec2VmSandboxProvider(
        ec2_client=ec2,
        control_channel=channel,
        worker_identity_verified=verifier,
        approved_ami_id="ami-0123456789abcdef0",
        control_security_group_id="sg-supervisor",
        subnet_by_lane={"solve": "subnet-a", "grading": "subnet-b", "admission": "subnet-c"},
        security_group_by_lane={"solve": "sg-a", "grading": "sg-b", "admission": "sg-c"},
        instance_type="m7i.large",
        candidate_image_digests={IMAGE: DIGEST},
    )
    handle = asyncio.run(provider.create(_spec()))
    assert handle.isolation_tier == "production"
    assert "IamInstanceProfile" not in ec2.call
    assert ec2.call["MetadataOptions"] == {"HttpEndpoint": "disabled", "HttpTokens": "required"}
    assert ec2.call["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False  # type: ignore[index]
    capability = provider._stage_capabilities[handle.sandbox_id]
    assert len(capability) >= 32
    assert capability not in handle.model_dump_json()

    asyncio.run(
        provider.stage_inputs(
            handle, InputManifest(files={"main.py": b"print(1)"}, max_total_bytes=8)
        )
    )
    assert channel.calls[-1][1]["stage_capability"] == capability
    result = asyncio.run(
        provider.execute(handle, ExecRequest(argv=("python", "main.py"), timeout_seconds=5))
    )
    assert result.isolation_tier == "production" and result.stdout == b"ok"
    attestation = asyncio.run(provider.attest(handle))
    assert attestation.verified_by == "trusted_worker_identity"
    snapshot = asyncio.run(provider.snapshot(handle))
    assert snapshot.archive_digest == f"sha256:{hashlib.sha256(b'').hexdigest()}"
    asyncio.run(provider.destroy(handle))
    assert handle.sandbox_id not in provider._stage_capabilities


@pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1", reason="live Docker containment is opt-in"
)
def test_live_docker_containment_and_cleanup() -> None:
    docker = shutil.which("docker")
    assert docker is not None

    def new_sandbox() -> tuple[LocalDockerSandboxProvider, SandboxHandle]:
        provider = LocalDockerSandboxProvider(
            allowed_images={IMAGE: DIGEST},
            state_dir=Path.cwd() / ".cache" / f"prompt06-live-{uuid4().hex}",
            provider_id="live-tests",
            operation_timeout_seconds=20,
        )
        handle = asyncio.run(provider.create(_spec(pids_limit=16)))
        return provider, handle

    provider, handle = new_sandbox()
    container_id = handle.resource_id
    try:
        asyncio.run(
            provider.stage_inputs(
                handle, InputManifest(files={"visible.txt": b"scoped fixture"}, max_total_bytes=14)
            )
        )
        asyncio.run(
            provider.stage_inputs(
                handle, InputManifest(files={"visible.txt": b"scoped fixture"}, max_total_bytes=14)
            )
        )
        staged = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=("python", "-c", "print(open('/workspace/visible.txt').read())"),
                    timeout_seconds=5,
                ),
            )
        )
        assert staged.exit_code == 0 and staged.stdout.strip() == b"scoped fixture"
        network = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=(
                        "python",
                        "-c",
                        "import socket; s=socket.socket(); s.settimeout(1); "
                        "[(lambda: None)() for _ in ()]; "
                        "exec(\"for ip in ('169.254.169.254','1.1.1.1'):\\n"
                        " try: s.connect((ip,80)); print('reachable',ip)\\n"
                        " except OSError: print('blocked',ip)\\n\")",
                    ),
                    timeout_seconds=5,
                ),
            )
        )
        assert network.exit_code == 0
        assert network.stdout.count(b"blocked") == 2
        assert b"reachable" not in network.stdout
        process_limit = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=(
                        "python",
                        "-c",
                        "import subprocess,sys; children=[]\n"
                        "try:\n"
                        " while True: children.append(subprocess.Popen("
                        "[sys.executable,'-c','import time;time.sleep(20)']))\n"
                        "except OSError: print('pids bounded',len(children)<16)\n"
                        "finally:\n"
                        " for child in children: child.terminate()\n"
                        " for child in children: child.wait()\n",
                    ),
                    timeout_seconds=10,
                ),
            )
        )
        assert process_limit.exit_code == 0
        assert b"pids bounded True" in process_limit.stdout
        socket_check = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=(
                        "python",
                        "-c",
                        "import os; print(os.path.exists('/var/run/docker.sock'))",
                    ),
                    timeout_seconds=5,
                ),
            )
        )
        assert socket_check.stdout.strip() == b"False"
        symlink = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=(
                        "python",
                        "-c",
                        "import os; os.symlink('/etc/passwd','/workspace/escape')",
                    ),
                    timeout_seconds=5,
                ),
            )
        )
        assert symlink.exit_code == 0, symlink.stderr.decode("utf-8", errors="replace")
        with pytest.raises(SandboxError, match="link or special"):
            asyncio.run(provider.snapshot(handle))

        disk = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=(
                        "python",
                        "-c",
                        "import errno; f=open('/workspace/full','wb'); "
                        "exec(\"try:\\n f.write(b'x'*(80*1024*1024))\\n"
                        "except OSError as e: print('bounded',e.errno==errno.ENOSPC)\\n\")",
                    ),
                    timeout_seconds=10,
                ),
            )
        )
        assert disk.exit_code == 0 and b"bounded True" in disk.stdout
    finally:
        asyncio.run(provider.destroy(handle))
    assert (
        subprocess.run(
            [docker, "inspect", container_id], capture_output=True, check=False, timeout=10
        ).returncode
        != 0
    )

    provider, handle = new_sandbox()
    try:
        timeout = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=("python", "-c", "import time; time.sleep(30)"), timeout_seconds=1
                ),
            )
        )
        assert timeout.timed_out is True
    finally:
        asyncio.run(provider.destroy(handle))

    provider, expired_handle = new_sandbox()
    expired = asyncio.run(provider.collect_expired(now_epoch=expired_handle.expires_at_epoch))
    assert expired == (expired_handle.sandbox_id,)
    assert (
        subprocess.run(
            [docker, "inspect", expired_handle.resource_id],
            capture_output=True,
            check=False,
            timeout=10,
        ).returncode
        != 0
    )

    provider, handle = new_sandbox()
    try:

        async def cancel_execution() -> None:
            task = asyncio.create_task(
                provider.execute(
                    handle,
                    ExecRequest(
                        argv=("python", "-c", "import time; time.sleep(30)"), timeout_seconds=20
                    ),
                )
            )
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_execution())
    finally:
        asyncio.run(provider.destroy(handle))

    provider, handle = new_sandbox()
    try:
        memory = asyncio.run(
            provider.execute(
                handle,
                ExecRequest(
                    argv=("python", "-c", "x=bytearray(400*1024*1024); print(len(x))"),
                    timeout_seconds=10,
                ),
            )
        )
        assert memory.exit_code != 0
    finally:
        asyncio.run(provider.destroy(handle))
