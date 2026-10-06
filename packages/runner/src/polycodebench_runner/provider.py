"""Bounded lifecycle providers. Docker-backed execution is development evidence only."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from polycodebench_runner.contracts import (
    ExecRequest,
    ExecResult,
    InputManifest,
    IsolationAttestation,
    SandboxHandle,
    SandboxSpec,
    WorkspaceFile,
    WorkspaceManifest,
)

LOGGER = logging.getLogger("polycodebench.sandbox")
MAX_STAGE_BYTES = 512 * 1024**2
MAX_SNAPSHOT_FILES = 100_000
STAGE_WRITER = (
    "import base64,json,os,stat,sys\n"
    "body=json.load(sys.stdin); files=body.get('files',{}); total=0\n"
    "if not isinstance(files,dict): sys.exit(42)\n"
    "root=os.open('/workspace',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n"
    "for name,value in sorted(files.items()):\n"
    " parts=name.split('/')\n"
    " if not name or '\\\\' in name or any(p in ('','.','..') for p in parts): sys.exit(43)\n"
    " if not isinstance(value,str): sys.exit(44)\n"
    " data=base64.b64decode(value,validate=True); total+=len(data)\n"
    " if total>body['max_total_bytes']: sys.exit(45)\n"
    " parent=os.dup(root)\n"
    " for part in parts[:-1]:\n"
    "  try: os.mkdir(part,0o700,dir_fd=parent)\n"
    "  except FileExistsError: pass\n"
    "  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)\n"
    "  os.close(parent); parent=child\n"
    " leaf=parts[-1]; flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW\n"
    " try: fd=os.open(leaf,flags,0o444,dir_fd=parent)\n"
    " except FileExistsError:\n"
    "  fd=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)\n"
    "  old=b''\n"
    "  while True:\n"
    "   chunk=os.read(fd,65536)\n"
    "   if not chunk: break\n"
    "   old+=chunk\n"
    "  os.close(fd)\n"
    "  if old!=data: sys.exit(46)\n"
    " else:\n"
    "  view=memoryview(data)\n"
    "  while view: view=view[os.write(fd,view):]\n"
    "  os.close(fd)\n"
    " os.close(parent)\n"
    "os.close(root); print('staged')\n"
)


SNAPSHOT_WRITER = (
    "import os,stat,sys,tarfile\n"
    "root='/workspace'; entries=[]\n"
    "for base,dirs,files in os.walk(root,followlinks=False):\n"
    " dirs.sort()\n"
    " for name in dirs+files:\n"
    "  path=os.path.join(base,name); mode=os.lstat(path).st_mode\n"
    "  if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)): sys.exit(41)\n"
    "  entries.append((os.path.relpath(path,root),path,mode))\n"
    "entries.sort()\n"
    "out=tarfile.open(fileobj=sys.stdout.buffer,mode='w|',format=tarfile.GNU_FORMAT)\n"
    "for rel,path,mode in entries:\n"
    " info=tarfile.TarInfo(rel); info.mtime=0; info.uid=info.gid=0; info.uname=info.gname=''\n"
    " info.mode=mode&0o7777\n"
    " if stat.S_ISDIR(mode):\n"
    "  info.type=tarfile.DIRTYPE; out.addfile(info)\n"
    " else:\n"
    "  info.size=os.path.getsize(path)\n"
    "  with open(path,'rb') as handle: out.addfile(info,handle)\n"
    "out.close()\n"
)


class SandboxError(RuntimeError):
    """A bounded, sanitized sandbox operation failure."""


def run_bounded_process(
    command: list[str],
    timeout: int,
    output_limit: int,
    input_bytes: bytes | None = None,
) -> tuple[int | None, bytes, bytes, bool]:
    """Drain child pipes continuously while retaining at most output_limit bytes each."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout = bytearray()
    stderr = bytearray()

    def drain(pipe: Any, destination: bytearray) -> None:
        while chunk := pipe.read(64 * 1024):
            remaining = output_limit - len(destination)
            if remaining > 0:
                destination.extend(chunk[:remaining])

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    writer: threading.Thread | None = None
    if input_bytes is not None and process.stdin is not None:
        input_pipe = process.stdin

        def write_input() -> None:
            try:
                input_pipe.write(input_bytes)
                input_pipe.close()
            except (BrokenPipeError, OSError):
                pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=5)
        if writer is not None:
            writer.join(timeout=5)
        return None, bytes(stdout), bytes(stderr), True
    for reader in readers:
        reader.join()
    if writer is not None:
        writer.join()
    return code, bytes(stdout), bytes(stderr), False


class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None: ...

    async def execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult: ...

    async def snapshot(self, handle: SandboxHandle) -> WorkspaceManifest: ...

    async def terminate(self, handle: SandboxHandle, reason: str) -> None: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...


class GuestControlChannel(Protocol):
    def invoke(
        self, private_ip: str, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...


class AwsWorkerIdentityVerifier:
    """Production badge gate bound to the supervisor's actual AWS principal ARN."""

    def __init__(self, *, sts_client: Any, expected_supervisor_arn: str) -> None:
        expected = re.fullmatch(
            r"arn:(?P<partition>aws[a-z-]*):iam::(?P<account>\d{12}):role/(?P<path>(?:[\w+=,.@-]+/)*)?(?P<role>[\w+=,.@-]+)",
            expected_supervisor_arn,
        )
        if expected is None or expected["path"]:
            raise ValueError("expected supervisor IAM role ARN is invalid")
        self.sts = sts_client
        self.expected_supervisor_arn = expected_supervisor_arn
        self._expected_partition = expected["partition"]
        self._expected_account = expected["account"]
        self._expected_role = expected["role"]

    def __call__(self) -> bool:
        identity = self.sts.get_caller_identity()
        caller_arn = str(identity.get("Arn", ""))
        assumed = re.fullmatch(
            r"arn:(?P<partition>aws[a-z-]*):sts::(?P<account>\d{12}):assumed-role/(?P<role>[\w+=,.@-]+)/(?P<session>[\w+=,.@-]+)",
            caller_arn,
        )
        iam_role = re.fullmatch(
            r"arn:(?P<partition>aws[a-z-]*):iam::(?P<account>\d{12}):role/(?P<path>(?:[\w+=,.@-]+/)*)?(?P<role>[\w+=,.@-]+)",
            caller_arn,
        )
        caller = assumed or iam_role
        return bool(
            caller
            and caller["partition"] == self._expected_partition
            and caller["account"] == self._expected_account
            and caller["role"] == self._expected_role
        )


class SshGuestControlChannel:
    """Restricted host-to-guest SSH transport; operation data travels on stdin."""

    def __init__(
        self,
        *,
        username: str,
        identity_file: Path,
        known_hosts_file: Path,
        timeout_seconds: int = 30,
        ssh_executable: str = "ssh",
    ) -> None:
        if not username.isascii() or not username.replace("-", "").isalnum():
            raise ValueError("SSH control username is invalid")
        if not identity_file.is_file() or not known_hosts_file.is_file():
            raise ValueError("SSH control identity and pinned known-hosts file are required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("SSH control timeout must be in [1,120]")
        self.username = username
        self.identity_file = identity_file.resolve()
        self.known_hosts_file = known_hosts_file.resolve()
        self.timeout_seconds = timeout_seconds
        self.ssh_executable = ssh_executable

    def invoke(self, private_ip: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        ip = ipaddress.ip_address(private_ip)
        if not ip.is_private or ip.is_loopback or ip.is_link_local:
            raise SandboxError("guest control address must be a private VPC address")
        if operation not in {"initialize", "stage", "execute", "snapshot", "terminate", "attest"}:
            raise SandboxError("guest control operation is not allowlisted")
        request = json.dumps(
            {"schema_version": 1, "operation": operation, "payload": payload},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(request) > MAX_STAGE_BYTES * 2:
            raise SandboxError("guest control request exceeds its byte limit")
        command = [
            self.ssh_executable,
            "-T",
            "-i",
            str(self.identity_file),
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oStrictHostKeyChecking=yes",
            "-oUserKnownHostsFile=" + str(self.known_hosts_file),
            "-oForwardAgent=no",
            "-oClearAllForwardings=yes",
            "-oPermitLocalCommand=no",
            "-oConnectTimeout=10",
            f"{self.username}@{ip}",
            "/usr/local/sbin/pcb-guest-control",
        ]
        try:
            result = subprocess.run(
                command,
                input=request,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError("guest control channel failed or timed out") from exc
        if result.returncode != 0 or len(result.stdout) > MAX_STAGE_BYTES * 2:
            raise SandboxError("guest control operation failed")
        try:
            reply = json.loads(result.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SandboxError("guest control returned an invalid response") from exc
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise SandboxError("guest control rejected the operation")
        return reply


class Ec2VmSandboxProvider:
    """EC2 lifecycle adapter requiring a verified worker identity and pinned SSH channel."""

    def __init__(
        self,
        *,
        ec2_client: Any,
        control_channel: GuestControlChannel,
        worker_identity_verified: AwsWorkerIdentityVerifier,
        approved_ami_id: str,
        launch_template_by_lane: dict[str, str],
        launch_template_version_by_lane: dict[str, int],
        environment: str,
        supervisor_role: str,
        control_security_group_id: str,
        subnet_by_lane: dict[str, str],
        security_group_by_lane: dict[str, str],
        instance_type: str,
        candidate_image_digests: dict[str, str],
        boot_timeout_seconds: int = 180,
    ) -> None:
        if not approved_ami_id.startswith("ami-") or not instance_type:
            raise ValueError("approved AMI and instance class are required")
        if set(launch_template_by_lane) != {"solve", "grading", "admission"}:
            raise ValueError("each execution lane requires its own approved launch template")
        if any(not value.startswith("lt-") for value in launch_template_by_lane.values()):
            raise ValueError("execution lane launch template ids are invalid")
        if len(set(launch_template_by_lane.values())) != 3:
            raise ValueError("execution lanes must not share launch templates")
        if set(launch_template_version_by_lane) != {"solve", "grading", "admission"}:
            raise ValueError("each execution lane requires a pinned launch template version")
        if any(value < 1 for value in launch_template_version_by_lane.values()):
            raise ValueError("execution lane launch template versions must be positive")
        if environment not in {"integration", "staging", "production"}:
            raise ValueError("execution environment is invalid")
        if supervisor_role not in {"solve-supervisor", "eval-supervisor", "admission-operator"}:
            raise ValueError("sandbox supervisor role is invalid")
        if set(subnet_by_lane) != {"solve", "grading", "admission"}:
            raise ValueError("each execution lane requires its own subnet")
        if set(security_group_by_lane) != {"solve", "grading", "admission"}:
            raise ValueError("each execution lane requires its own security group")
        if len(set(subnet_by_lane.values())) != 3 or len(set(security_group_by_lane.values())) != 3:
            raise ValueError("execution lanes must not share subnets or security groups")
        self.ec2 = ec2_client
        self.channel = control_channel
        self.worker_identity_verified = worker_identity_verified
        self.approved_ami_id = approved_ami_id
        self.launch_template_by_lane = dict(launch_template_by_lane)
        self.launch_template_version_by_lane = dict(launch_template_version_by_lane)
        self.environment = environment
        self.supervisor_role = supervisor_role
        self.control_security_group_id = control_security_group_id
        self.subnet_by_lane = dict(subnet_by_lane)
        self.security_group_by_lane = dict(security_group_by_lane)
        self.instance_type = instance_type
        self.candidate_image_digests = dict(candidate_image_digests)
        self.boot_timeout_seconds = boot_timeout_seconds
        self._stage_capabilities: dict[str, str] = {}

    def _require_worker(self) -> None:
        if not self.worker_identity_verified():
            raise SandboxError("production sandbox requires verified production worker identity")

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return await asyncio.to_thread(self._create, spec)

    def _create(self, spec: SandboxSpec) -> SandboxHandle:
        self._require_worker()
        if spec.disk_bytes > MAX_STAGE_BYTES:
            raise SandboxError("EC2 worker policy disk cap is 512 MiB")
        if self.candidate_image_digests.get(spec.image) != spec.image_digest:
            raise SandboxError("candidate image is not approved for production execution")
        sandbox_id = str(uuid4())
        expires = int(time.time()) + spec.ttl_seconds
        stage_capability = secrets.token_urlsafe(32)
        response = self.ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateId": self.launch_template_by_lane[spec.lane],
                "Version": str(self.launch_template_version_by_lane[spec.lane]),
            },
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "pcb:owner", "Value": "polycodebench"},
                        {"Key": "pcb:environment", "Value": self.environment},
                        {"Key": "pcb:role", "Value": self.supervisor_role},
                        {"Key": "pcb:sandbox", "Value": sandbox_id},
                        {"Key": "pcb:stage", "Value": spec.stage_id},
                        {"Key": "pcb:fence", "Value": str(spec.fence)},
                        {"Key": "pcb:lane", "Value": spec.lane},
                        {"Key": "pcb:expires", "Value": str(expires)},
                        {"Key": "pcb:timeout", "Value": str(spec.timeout_seconds)},
                    ],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "pcb:owner", "Value": "polycodebench"},
                        {"Key": "pcb:environment", "Value": self.environment},
                        {"Key": "pcb:role", "Value": self.supervisor_role},
                        {"Key": "pcb:sandbox", "Value": sandbox_id},
                        {"Key": "pcb:stage", "Value": spec.stage_id},
                        {"Key": "pcb:fence", "Value": str(spec.fence)},
                        {"Key": "pcb:lane", "Value": spec.lane},
                        {"Key": "pcb:expires", "Value": str(expires)},
                        {"Key": "pcb:timeout", "Value": str(spec.timeout_seconds)},
                    ],
                },
            ],
        )
        instances = response.get("Instances", [])
        if len(instances) != 1 or not instances[0].get("InstanceId"):
            raise SandboxError("EC2 did not return exactly one instance")
        instance_id = instances[0]["InstanceId"]
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            stage_id=spec.stage_id,
            fence=spec.fence,
            lane=spec.lane,
            driver="ec2_vm",
            resource_id=instance_id,
            expires_at_epoch=expires,
            max_execution_seconds=spec.timeout_seconds,
            isolation_tier="production",
        )
        try:
            deadline = time.monotonic() + self.boot_timeout_seconds
            private_ip: str | None = None
            guest_ready = False
            while time.monotonic() < deadline:
                self._require_worker()
                current = self._instance(handle)
                if current and current.get("State", {}).get("Name") == "running":
                    # Check AWS-side isolation before sending any guest-control request.
                    self._verify_aws_instance_policy(handle)
                    private_ip = current.get("PrivateIpAddress")
                    if private_ip:
                        try:
                            self.channel.invoke(
                                private_ip,
                                "initialize",
                                {
                                    "sandbox_id": sandbox_id,
                                    "stage_id": spec.stage_id,
                                    "fence": spec.fence,
                                    "lane": spec.lane,
                                    "image": spec.image,
                                    "image_digest": spec.image_digest,
                                    "cpu_millis": spec.cpu_millis,
                                    "memory_bytes": spec.memory_bytes,
                                    "disk_bytes": spec.disk_bytes,
                                    "pids_limit": spec.pids_limit,
                                    "timeout_seconds": spec.timeout_seconds,
                                    "expires_at_epoch": expires,
                                    "stage_capability": stage_capability,
                                },
                            )
                            guest_ready = True
                            break
                        except SandboxError:
                            time.sleep(2)
            if private_ip is None or not guest_ready:
                raise SandboxError("disposable VM did not become ready before its deadline")
        except BaseException:
            self._stage_capabilities[sandbox_id] = stage_capability
            try:
                self._destroy(handle)
            except Exception:
                LOGGER.exception(
                    "failed guest cleanup could not be verified instance=%s", instance_id
                )
            raise
        self._stage_capabilities[sandbox_id] = stage_capability
        try:
            self._attest(handle)
        except BaseException:
            self._destroy(handle)
            raise
        return handle

    def _instance(self, handle: SandboxHandle) -> dict[str, Any]:
        self._require_worker()
        described = self.ec2.describe_instances(InstanceIds=[handle.resource_id])
        reservations = described.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            raise SandboxError("EC2 instance identity cannot be verified")
        instance = reservations[0]["Instances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
        expected = {
            "pcb:owner": "polycodebench",
            "pcb:environment": self.environment,
            "pcb:role": self.supervisor_role,
            "pcb:sandbox": handle.sandbox_id,
            "pcb:stage": handle.stage_id,
            "pcb:fence": str(handle.fence),
            "pcb:lane": handle.lane,
        }
        if any(tags.get(key) != value for key, value in expected.items()):
            raise SandboxError("EC2 resource owner/stage/fence mismatch")
        return dict(instance)

    def _private_ip(self, handle: SandboxHandle) -> str:
        instance = self._instance(handle)
        ip = instance.get("PrivateIpAddress")
        if not ip:
            raise SandboxError("EC2 instance has no private control address")
        return str(ip)

    def _scoped_payload(self, handle: SandboxHandle) -> dict[str, Any]:
        capability = self._stage_capabilities.get(handle.sandbox_id)
        if not capability:
            raise SandboxError("stage-scoped guest capability is unavailable")
        return {
            "sandbox_id": handle.sandbox_id,
            "stage_id": handle.stage_id,
            "fence": handle.fence,
            "stage_capability": capability,
        }

    async def stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None:
        await asyncio.to_thread(self._stage_inputs, handle, manifest)

    def _stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None:
        if handle.driver != "ec2_vm" or sum(map(len, manifest.files.values())) > min(
            manifest.max_total_bytes, MAX_STAGE_BYTES
        ):
            raise SandboxError("invalid EC2 stage scope or input limit")
        self._attest(handle)
        encoded = {
            path: base64.b64encode(data).decode("ascii") for path, data in manifest.files.items()
        }
        payload = self._scoped_payload(handle)
        payload["files"] = encoded
        self.channel.invoke(self._private_ip(handle), "stage", payload)

    async def execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        try:
            return await asyncio.to_thread(self._execute, handle, request)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._terminate, handle, "caller_cancelled"))
            raise

    def _execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        self._attest(handle)
        start = time.monotonic()
        payload = self._scoped_payload(handle)
        payload.update(
            {
                "argv": list(request.argv),
                "timeout_seconds": request.timeout_seconds,
                "environment": request.environment,
                "max_output_bytes": request.max_output_bytes,
            }
        )
        reply = self.channel.invoke(self._private_ip(handle), "execute", payload)
        return ExecResult(
            exit_code=reply.get("exit_code"),
            stdout=base64.b64decode(reply.get("stdout_b64", ""), validate=True)[
                : request.max_output_bytes
            ],
            stderr=base64.b64decode(reply.get("stderr_b64", ""), validate=True)[
                : request.max_output_bytes
            ],
            timed_out=reply.get("timed_out") is True,
            duration_ms=int((time.monotonic() - start) * 1000),
            isolation_tier="production",
            sandbox_id=handle.sandbox_id,
        )

    async def snapshot(self, handle: SandboxHandle) -> WorkspaceManifest:
        return await asyncio.to_thread(self._snapshot, handle)

    def _snapshot(self, handle: SandboxHandle) -> WorkspaceManifest:
        self._attest(handle)
        reply = self.channel.invoke(
            self._private_ip(handle), "snapshot", self._scoped_payload(handle)
        )
        files = tuple(WorkspaceFile.model_validate(row) for row in reply["files"])
        return WorkspaceManifest(
            sandbox_id=handle.sandbox_id,
            captured_at_epoch=int(time.time()),
            files=files,
            total_bytes=sum(file.size_bytes for file in files),
            archive_digest=reply["archive_digest"],
            archive_bytes=base64.b64decode(reply["archive_b64"], validate=True),
            isolation_tier="production",
        )

    async def terminate(self, handle: SandboxHandle, reason: str) -> None:
        await asyncio.to_thread(self._terminate, handle, reason)

    def _terminate(self, handle: SandboxHandle, reason: str) -> None:
        if not reason or len(reason) > 128:
            raise SandboxError("termination reason is invalid")
        instance = self._instance(handle)
        if instance.get("State", {}).get("Name") == "terminated":
            self._stage_capabilities.pop(handle.sandbox_id, None)
            return
        if instance.get("State", {}).get("Name") == "running":
            try:
                payload = self._scoped_payload(handle)
                payload["reason"] = reason
                self.channel.invoke(self._private_ip(handle), "terminate", payload)
            except SandboxError:
                # Cloud destruction remains mandatory if guest control is unavailable.
                LOGGER.warning(
                    "guest termination acknowledgement unavailable sandbox=%s",
                    handle.sandbox_id,
                )
        self.ec2.terminate_instances(InstanceIds=[handle.resource_id])

    async def destroy(self, handle: SandboxHandle) -> None:
        await asyncio.to_thread(self._destroy, handle)

    def _destroy(self, handle: SandboxHandle) -> None:
        self._require_worker()
        described = self.ec2.describe_instances(InstanceIds=[handle.resource_id])
        reservations = described.get("Reservations", [])
        instances = reservations[0].get("Instances", []) if reservations else []
        if not instances:
            self._stage_capabilities.pop(handle.sandbox_id, None)
            return
        self._instance(handle)
        if instances[0].get("State", {}).get("Name") == "terminated":
            self._stage_capabilities.pop(handle.sandbox_id, None)
            return
        self.ec2.terminate_instances(InstanceIds=[handle.resource_id])
        deadline = time.monotonic() + self.boot_timeout_seconds
        while time.monotonic() < deadline:
            response = self.ec2.describe_instances(InstanceIds=[handle.resource_id])
            instances = response.get("Reservations", [{}])[0].get("Instances", [])
            if not instances or instances[0].get("State", {}).get("Name") == "terminated":
                self._stage_capabilities.pop(handle.sandbox_id, None)
                return
            time.sleep(2)
        raise SandboxError("EC2 destruction could not be verified; capacity cannot be reused")

    async def attest(self, handle: SandboxHandle) -> IsolationAttestation:
        return await asyncio.to_thread(self._attest, handle)

    def _verify_aws_instance_policy(self, handle: SandboxHandle) -> dict[str, Any]:
        instance = self._instance(handle)
        metadata = instance.get("MetadataOptions", {})
        interfaces = instance.get("NetworkInterfaces", [])
        attached_groups = sorted(
            group.get("GroupId", "")
            for interface in interfaces
            for group in interface.get("Groups", [])
        )
        expected_group = self.security_group_by_lane[handle.lane]
        if (
            metadata.get("HttpEndpoint") != "disabled"
            or instance.get("IamInstanceProfile") is not None
            or instance.get("PublicIpAddress") is not None
            or instance.get("SubnetId") != self.subnet_by_lane[handle.lane]
            or instance.get("InstanceType") != self.instance_type
            or instance.get("ImageId") != self.approved_ami_id
            or attached_groups != [expected_group]
            or any(interface.get("Association", {}).get("PublicIp") for interface in interfaces)
        ):
            raise SandboxError("EC2 instance does not meet the no-role/no-metadata/private policy")
        security_groups = self.ec2.describe_security_groups(GroupIds=[expected_group]).get(
            "SecurityGroups", []
        )
        if len(security_groups) != 1:
            raise SandboxError("EC2 lane security group cannot be verified")
        group = security_groups[0]
        ingress = group.get("IpPermissions", [])
        egress = group.get("IpPermissionsEgress", [])
        allowed_control = (
            len(ingress) == 1
            and ingress[0].get("IpProtocol") == "tcp"
            and ingress[0].get("FromPort") == 22
            and ingress[0].get("ToPort") == 22
            and [item.get("GroupId") for item in ingress[0].get("UserIdGroupPairs", [])]
            == [self.control_security_group_id]
            and not ingress[0].get("IpRanges")
            and not ingress[0].get("Ipv6Ranges")
            and not ingress[0].get("PrefixListIds")
        )
        if egress or not allowed_control or group.get("VpcId") != instance.get("VpcId"):
            raise SandboxError(
                "EC2 lane security group is not restricted to supervisor control ingress"
            )
        return instance

    def _attest(self, handle: SandboxHandle) -> IsolationAttestation:
        self._verify_aws_instance_policy(handle)
        guest = self.channel.invoke(
            self._private_ip(handle), "attest", self._scoped_payload(handle)
        )
        controls = guest.get("controls")
        if not isinstance(controls, list) or set(controls) != {
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
        }:
            raise SandboxError("guest did not attest every required isolation control")
        policy_bytes = json.dumps(
            {
                "instance": "private-no-role-no-metadata",
                "control_channel_only": True,
                "guest_controls": sorted(controls),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return IsolationAttestation(
            sandbox_id=handle.sandbox_id,
            driver="ec2_vm",
            isolation_tier="production",
            policy_digest=f"sha256:{hashlib.sha256(policy_bytes).hexdigest()}",
            controls=tuple(sorted([*controls, "vm_private_subnet", "vm_security_group"])),
            verified_by="trusted_worker_identity",
        )

    async def collect_expired(self, *, now_epoch: int | None = None) -> tuple[str, ...]:
        cutoff = int(time.time()) if now_epoch is None else now_epoch
        return await asyncio.to_thread(self._collect_expired, cutoff)

    def _collect_expired(self, now_epoch: int) -> tuple[str, ...]:
        self._require_worker()
        response = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:pcb:owner", "Values": ["polycodebench"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        removed: list[str] = []
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
                if tags.get("pcb:owner") != "polycodebench":
                    continue
                if (
                    not tags.get("pcb:expires", "").isdigit()
                    or int(tags["pcb:expires"]) > now_epoch
                ):
                    continue
                instance_id = instance["InstanceId"]
                lane = tags.get("pcb:lane")
                if lane not in {"solve", "grading", "admission"}:
                    continue
                handle = SandboxHandle(
                    sandbox_id=tags["pcb:sandbox"],
                    stage_id=tags["pcb:stage"],
                    fence=int(tags["pcb:fence"]),
                    lane=lane,
                    driver="ec2_vm",
                    resource_id=instance_id,
                    expires_at_epoch=int(tags["pcb:expires"]),
                    max_execution_seconds=int(tags["pcb:timeout"]),
                    isolation_tier="production",
                )
                self._destroy(handle)
                removed.append(instance_id)
        return tuple(removed)


class LocalDockerSandboxProvider:
    """A strict, development-only Docker driver using a private tmpfs workspace."""

    def __init__(
        self,
        *,
        allowed_images: dict[str, str],
        state_dir: Path,
        provider_id: str = "local-default",
        operation_timeout_seconds: int = 30,
        docker_executable: str = "docker",
    ) -> None:
        if not allowed_images:
            raise ValueError("at least one approved immutable image is required")
        for image, digest in allowed_images.items():
            if "@sha256:" not in image or not image.endswith(digest.removeprefix("sha256:")):
                raise ValueError("image allowlist entries must be digest-pinned and consistent")
        if not 1 <= operation_timeout_seconds <= 120:
            raise ValueError("Docker operation timeout must be in [1,120]")
        if not provider_id.replace("-", "").isalnum():
            raise ValueError("provider_id must be a safe label value")
        self.allowed_images = dict(allowed_images)
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.provider_id = provider_id
        self.operation_timeout_seconds = operation_timeout_seconds
        self.docker_executable = docker_executable
        self._handles: dict[str, SandboxHandle] = {}

    def _docker(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        try:
            code, stdout, _stderr, timed_out = run_bounded_process(
                [self.docker_executable, *args],
                self.operation_timeout_seconds,
                MAX_STAGE_BYTES + 1,
                input_bytes,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError("Docker operation failed or exceeded its timeout") from exc
        if timed_out or code != 0 or len(stdout) > MAX_STAGE_BYTES:
            # Do not echo daemon output, which can contain host paths or operational details.
            raise SandboxError(f"Docker operation failed (exit {code})")
        return stdout

    def _assert_owned(self, handle: SandboxHandle) -> None:
        if handle.driver != "local_docker" or handle.isolation_tier != "development":
            raise SandboxError("handle is not a development Docker sandbox")
        known = self._handles.get(handle.sandbox_id)
        if known is not None and known != handle:
            raise SandboxError("sandbox handle scope mismatch")
        inspected = self._docker(
            ["inspect", "--format", "{{json .Config.Labels}}", handle.resource_id]
        )
        try:
            labels = json.loads(inspected)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SandboxError("Docker returned invalid ownership metadata") from exc
        if (
            labels.get("pcb.provider") != self.provider_id
            or labels.get("pcb.sandbox") != handle.sandbox_id
            or labels.get("pcb.stage") != handle.stage_id
            or labels.get("pcb.fence") != str(handle.fence)
            or labels.get("pcb.lane") != handle.lane
        ):
            raise SandboxError("sandbox resource ownership or fence mismatch")

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return await asyncio.to_thread(self._create, spec)

    def _create(self, spec: SandboxSpec) -> SandboxHandle:
        if spec.disk_bytes > MAX_STAGE_BYTES:
            raise SandboxError("local development sandbox disk cap is 512 MiB")
        if (
            spec.image not in self.allowed_images
            or self.allowed_images[spec.image] != spec.image_digest
        ):
            raise SandboxError("runtime image is not approved for local development")
        sandbox_id = str(uuid4())
        name = f"pcb-{sandbox_id}"
        expires = int(time.time()) + spec.ttl_seconds
        cmd = [
            "create",
            "--name",
            name,
            "--label",
            "pcb.owner=polycodebench",
            "--label",
            f"pcb.provider={self.provider_id}",
            "--label",
            f"pcb.sandbox={sandbox_id}",
            "--label",
            f"pcb.stage={spec.stage_id}",
            "--label",
            f"pcb.fence={spec.fence}",
            "--label",
            f"pcb.lane={spec.lane}",
            "--label",
            f"pcb.expires={expires}",
            "--label",
            f"pcb.timeout={spec.timeout_seconds}",
            "--label",
            "pcb.tier=development",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit",
            str(spec.pids_limit),
            "--memory",
            str(spec.memory_bytes),
            "--memory-swap",
            str(spec.memory_bytes),
            "--cpus",
            f"{spec.cpu_millis / 1000:.3f}",
            "--user",
            "65532:65532",
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,uid=65532,gid=65532,mode=0700,size={spec.disk_bytes}"
            + (",exec" if spec.executable_workspace else ""),
            "--tmpfs",
            " /tmp:rw,noexec,nosuid,nodev,size=16m".strip(),
            "--workdir",
            "/workspace",
            "--entrypoint",
            "python",
            spec.image,
            "-I",
            "-B",
            "-S",
            "-c",
            "import time; time.sleep(86400)",
        ]
        cid = self._docker(cmd).decode("ascii").strip()
        if not cid or len(cid) > 128:
            raise SandboxError("Docker returned an invalid container identity")
        try:
            self._docker(["start", cid])
        except SandboxError:
            try:
                self._docker(["rm", "--force", cid])
            finally:
                raise
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            stage_id=spec.stage_id,
            fence=spec.fence,
            lane=spec.lane,
            driver="local_docker",
            resource_id=cid,
            expires_at_epoch=expires,
            max_execution_seconds=spec.timeout_seconds,
            isolation_tier="development",
        )
        self._handles[sandbox_id] = handle
        self._write_state(handle)
        LOGGER.info(
            "sandbox created id=%s stage=%s fence=%s lane=%s",
            sandbox_id,
            spec.stage_id,
            spec.fence,
            spec.lane,
        )
        return handle

    def _write_state(self, handle: SandboxHandle) -> None:
        state = self.state_dir / f"{handle.sandbox_id}.json"
        tmp = state.with_suffix(".tmp")
        tmp.write_text(handle.model_dump_json(), encoding="utf-8")
        os.replace(tmp, state)

    async def stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None:
        await asyncio.to_thread(self._stage_inputs, handle, manifest)

    def _stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None:
        self._assert_owned(handle)
        total = sum(len(data) for data in manifest.files.values())
        if total > min(manifest.max_total_bytes, MAX_STAGE_BYTES):
            raise SandboxError("stage input exceeds its total byte limit")
        request = json.dumps(
            {
                "files": {
                    path: base64.b64encode(data).decode("ascii")
                    for path, data in sorted(manifest.files.items())
                },
                "max_total_bytes": min(manifest.max_total_bytes, MAX_STAGE_BYTES),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        response = self._docker(
            [
                "exec",
                "-i",
                handle.resource_id,
                "python",
                "-I",
                "-B",
                "-S",
                "-c",
                STAGE_WRITER,
            ],
            input_bytes=request,
        )
        if response.strip() != b"staged":
            raise SandboxError("guest did not confirm input staging")
        LOGGER.info(
            "sandbox inputs staged id=%s files=%d bytes=%d",
            handle.sandbox_id,
            len(manifest.files),
            total,
        )

    async def execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        try:
            return await asyncio.to_thread(self._execute, handle, request)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._terminate, handle, "caller_cancelled"))
            raise

    def _execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        self._assert_owned(handle)
        started = time.monotonic()
        env = [
            arg
            for key, value in sorted(request.environment.items())
            for arg in ("--env", f"{key}={value}")
        ]
        try:
            exit_code, stdout, stderr, timed_out = run_bounded_process(
                [self.docker_executable, "exec", *env, handle.resource_id, *request.argv],
                min(
                    request.timeout_seconds,
                    handle.max_execution_seconds,
                    self.operation_timeout_seconds,
                ),
                request.max_output_bytes,
            )
            if timed_out:
                self._terminate(handle, "execution_timeout")
            return ExecResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                duration_ms=int((time.monotonic() - started) * 1000),
                isolation_tier="development",
                sandbox_id=handle.sandbox_id,
            )
        except OSError as exc:
            raise SandboxError("Docker execution client could not be started") from exc

    async def snapshot(self, handle: SandboxHandle) -> WorkspaceManifest:
        return await asyncio.to_thread(self._snapshot, handle)

    def _snapshot(self, handle: SandboxHandle) -> WorkspaceManifest:
        self._assert_owned(handle)
        # The workspace is a tmpfs, which `docker cp` cannot read (it returns an empty archive).
        # The archive is therefore produced inside the guest and read from stdout. The guest
        # writer refuses links and special files before emitting a single byte.
        try:
            raw = self._docker(
                ["exec", handle.resource_id, "python", "-I", "-B", "-S", "-c", SNAPSHOT_WRITER]
            )
        except SandboxError as exc:
            raise SandboxError("workspace contains link or special file, or is too large") from exc
        files: list[WorkspaceFile] = []
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
                members = archive.getmembers()
                if len(members) > MAX_SNAPSHOT_FILES:
                    raise SandboxError("workspace snapshot exceeds file count limit")
                seen: set[str] = set()
                for member in members:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                        raise SandboxError("workspace contains an unsafe path")
                    if member.name in seen:
                        raise SandboxError("workspace contains a duplicate path")
                    seen.add(member.name)
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise SandboxError("workspace contains a link or special file")
                    total += member.size
                    if total > MAX_STAGE_BYTES:
                        raise SandboxError("workspace snapshot exceeds byte limit")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise SandboxError("workspace snapshot contains unreadable file")
                    digest = hashlib.sha256(stream.read()).hexdigest()
                    files.append(
                        WorkspaceFile(
                            path=member.name, size_bytes=member.size, digest=f"sha256:{digest}"
                        )
                    )
        except (tarfile.TarError, OSError) as exc:
            raise SandboxError("workspace snapshot is malformed") from exc
        files.sort(key=lambda item: item.path)
        return WorkspaceManifest(
            sandbox_id=handle.sandbox_id,
            captured_at_epoch=int(time.time()),
            files=tuple(files),
            total_bytes=total,
            archive_digest=f"sha256:{hashlib.sha256(raw).hexdigest()}",
            archive_bytes=raw,
            isolation_tier="development",
        )

    async def terminate(self, handle: SandboxHandle, reason: str) -> None:
        await asyncio.to_thread(self._terminate, handle, reason)

    def _terminate(self, handle: SandboxHandle, reason: str) -> None:
        if not reason or len(reason) > 128 or any(ord(char) < 32 for char in reason):
            raise SandboxError("termination reason is invalid")
        self._assert_owned(handle)
        state = self._docker(["inspect", "--format", "{{json .State}}", handle.resource_id])
        if not json.loads(state).get("Running", False):
            return
        # Docker stop is idempotent for already stopped containers. Stop timeout is zero:
        # candidate process trees must not get an unbounded graceful-shutdown window.
        try:
            self._docker(["stop", "--time", "0", handle.resource_id])
        except SandboxError as exc:
            if "not running" not in str(exc):
                raise
        LOGGER.info("sandbox terminated id=%s reason=%s", handle.sandbox_id, reason)

    async def destroy(self, handle: SandboxHandle) -> None:
        await asyncio.to_thread(self._destroy, handle)

    def _destroy(self, handle: SandboxHandle) -> None:
        try:
            self._assert_owned(handle)
        except SandboxError as exc:
            try:
                self._docker(["inspect", handle.resource_id])
            except SandboxError:
                self._forget(handle)
                return
            raise exc
        try:
            self._docker(["rm", "--force", handle.resource_id])
        except SandboxError as exc:
            # Repeated destroy is idempotent only when inspect proves it is absent.
            try:
                self._docker(["inspect", handle.resource_id])
            except SandboxError:
                self._forget(handle)
                return
            raise exc
        try:
            self._docker(["inspect", handle.resource_id])
        except SandboxError:
            self._forget(handle)
            LOGGER.info("sandbox destroyed id=%s", handle.sandbox_id)
            return
        raise SandboxError("Docker container still exists after destruction")

    def _forget(self, handle: SandboxHandle) -> None:
        self._handles.pop(handle.sandbox_id, None)
        (self.state_dir / f"{handle.sandbox_id}.json").unlink(missing_ok=True)

    async def collect_expired(self, *, now_epoch: int | None = None) -> tuple[str, ...]:
        cutoff = int(time.time()) if now_epoch is None else now_epoch
        return await asyncio.to_thread(self._collect_expired, cutoff)

    def _collect_expired(self, now_epoch: int) -> tuple[str, ...]:
        raw = self._docker(
            ["ps", "--all", "--filter", "label=pcb.owner=polycodebench", "--format", "{{.ID}}"]
        )
        removed: list[str] = []
        for cid in raw.decode("ascii").splitlines():
            try:
                labels = json.loads(
                    self._docker(["inspect", "--format", "{{json .Config.Labels}}", cid])
                )
                if labels.get("pcb.provider") != self.provider_id:
                    continue
                if int(labels["pcb.expires"]) > now_epoch:
                    continue
                handle = SandboxHandle(
                    sandbox_id=labels["pcb.sandbox"],
                    stage_id=labels["pcb.stage"],
                    fence=int(labels["pcb.fence"]),
                    lane=labels["pcb.lane"],
                    driver="local_docker",
                    resource_id=cid,
                    expires_at_epoch=int(labels["pcb.expires"]),
                    max_execution_seconds=int(labels["pcb.timeout"]),
                    isolation_tier="development",
                )
                self._handles[handle.sandbox_id] = handle
                self._destroy(handle)
                removed.append(handle.sandbox_id)
            except (SandboxError, KeyError, ValueError, json.JSONDecodeError):
                LOGGER.exception("expired sandbox cleanup could not be verified resource=%s", cid)
        return tuple(removed)

    def attestation(self, handle: SandboxHandle) -> IsolationAttestation:
        self._assert_owned(handle)
        policy = {
            "network": "none",
            "read_only_root": True,
            "capabilities": "drop-all",
            "no_new_privileges": True,
            "user": "65532:65532",
            "tmpfs_workspace": True,
        }
        policy_bytes = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        return IsolationAttestation(
            sandbox_id=handle.sandbox_id,
            driver="local_docker",
            isolation_tier="development",
            policy_digest=f"sha256:{hashlib.sha256(policy_bytes).hexdigest()}",
            controls=tuple(sorted(policy)),
            verified_by="local_driver",
        )
