#!/usr/bin/env python3
"""Forced-command SSH guest supervisor. Candidate data is never shell text."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import PurePosixPath
from typing import Any, BinaryIO, cast

MAX_REQUEST = 768 * 1024**2
MAX_OUTPUT = 8 * 1024**2
MAX_FILES = 100_000
STATE_PATH = "/run/pcb-sandbox.json"
STAGE_WRITER = (
    "import base64,json,os,sys\n"
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
    "  fd=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent); old=b''\n"
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


class GuestError(Exception):
    pass


def bounded_process(
    command: list[str],
    timeout: int,
    output_limit: int,
    input_bytes: bytes | None = None,
) -> tuple[int | None, bytes, bytes, bool]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout = bytearray()
    stderr = bytearray()

    def drain(pipe: BinaryIO, target: bytearray) -> None:
        while chunk := pipe.read(64 * 1024):
            remaining = output_limit - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])

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

        def write_request() -> None:
            try:
                input_pipe.write(input_bytes)
                input_pipe.close()
            except (BrokenPipeError, OSError):
                pass

        writer = threading.Thread(target=write_request, daemon=True)
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


def docker(args: list[str], *, input_bytes: bytes | None = None, timeout: int = 30) -> bytes:
    code, stdout, _stderr, timed_out = bounded_process(
        ["/usr/bin/docker", *args], timeout, 512 * 1024**2 + 1, input_bytes
    )
    if timed_out or code != 0 or len(stdout) > 512 * 1024**2:
        raise GuestError("guest container operation failed or exceeded its byte/time bound")
    return stdout


def identity(payload: dict[str, object]) -> dict[str, Any]:
    try:
        with open(STATE_PATH, encoding="utf-8") as state_file:
            state = cast(dict[str, Any], json.load(state_file))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuestError("sandbox has not been initialized") from exc
    for name in ("sandbox_id", "stage_id", "fence"):
        if payload.get(name) != state.get(name):
            raise GuestError("guest sandbox scope mismatch")
    supplied = payload.get("stage_capability")
    if not isinstance(supplied, str) or not hmac.compare_digest(
        supplied, str(state.get("stage_capability", ""))
    ):
        raise GuestError("guest stage capability is invalid")
    if int(state.get("expires_at_epoch", 0)) <= int(time.time()):
        raise GuestError("guest stage capability has expired")
    return state


def safe_name(value: str) -> None:
    path = PurePosixPath(value)
    segments = value.split("/")
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in value
        or "\0" in value
        or any(part in {"", ".", ".."} for part in segments)
    ):
        raise GuestError("unsafe workspace path")


def handle(request: dict[str, object]) -> dict[str, object]:
    if request.get("schema_version") != 1 or not isinstance(request.get("payload"), dict):
        raise GuestError("invalid control request")
    operation = request.get("operation")
    payload = request["payload"]
    if not isinstance(payload, dict):
        raise GuestError("invalid control payload")
    if operation == "initialize":
        if re.fullmatch(r"[0-9a-f-]{36}", str(payload.get("sandbox_id"))) is None:
            raise GuestError("invalid sandbox identity")
        image = payload.get("image")
        digest = payload.get("image_digest")
        if (
            not isinstance(image, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or "@sha256:" not in image
            or not image.endswith(digest[7:])
        ):
            raise GuestError("candidate image is not pinned")
        specs = {
            "cpu_millis": (100, 64_000),
            "memory_bytes": (64 * 1024**2, 256 * 1024**3),
            "disk_bytes": (16 * 1024**2, 1024**4),
            "pids_limit": (1, 4096),
            "timeout_seconds": (1, 86_400),
            "expires_at_epoch": (0, 2**63 - 1),
        }
        for key, (minimum, maximum) in specs.items():
            value = payload.get(key)
            if type(value) is not int or not minimum <= value <= maximum:
                raise GuestError("resource limit is invalid")
        labels = {
            "sandbox_id": payload["sandbox_id"],
            "stage_id": payload["stage_id"],
            "fence": payload["fence"],
            "lane": payload["lane"],
        }
        capability = payload.get("stage_capability")
        if not isinstance(capability, str) or len(capability) < 32:
            raise GuestError("stage capability is invalid")
        state = {
            **labels,
            "image": image,
            "image_digest": digest,
            "stage_capability": capability,
            **{k: payload[k] for k in specs},
        }
        with open(STATE_PATH, "x", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, separators=(",", ":"))
        args = [
            "create",
            "--name",
            f"pcb-{payload['sandbox_id']}",
            "--label",
            f"pcb.sandbox={payload['sandbox_id']}",
            "--label",
            f"pcb.stage={payload['stage_id']}",
            "--label",
            f"pcb.fence={payload['fence']}",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit",
            str(payload["pids_limit"]),
            "--memory",
            str(payload["memory_bytes"]),
            "--memory-swap",
            str(payload["memory_bytes"]),
            "--cpus",
            f"{payload['cpu_millis'] / 1000:.3f}",
            "--user",
            "65532:65532",
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,uid=65532,gid=65532,mode=0700,size={payload['disk_bytes']}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "python",
            str(image),
            "-I",
            "-B",
            "-S",
            "-c",
            "import time; time.sleep(86400)",
        ]
        container_id = docker(args).decode("ascii").strip()
        docker(["start", container_id])
        state["container_id"] = container_id
        with open(STATE_PATH, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, separators=(",", ":"))
        return {"ok": True}

    state = identity(payload)
    guest_container_value = state.get("container_id")
    if not isinstance(guest_container_value, str):
        raise GuestError("sandbox container is unavailable")
    container_id = guest_container_value
    if operation == "stage":
        raw_files = payload.get("files")
        if not isinstance(raw_files, dict) or len(raw_files) > MAX_FILES:
            raise GuestError("staged file list is invalid")
        files = cast(dict[str, Any], raw_files)
        total = 0
        for name, value in files.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise GuestError("staged file entry is invalid")
            safe_name(name)
            try:
                total += len(base64.b64decode(value, validate=True))
            except ValueError as exc:
                raise GuestError("staged file encoding is invalid") from exc
            if total > min(int(state["disk_bytes"]), 512 * 1024**2):
                raise GuestError("staged data exceeds byte limit")
        request_bytes = json.dumps(
            {"files": files, "max_total_bytes": min(int(state["disk_bytes"]), 512 * 1024**2)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        response = docker(
            ["exec", "-i", container_id, "python", "-I", "-B", "-S", "-c", STAGE_WRITER],
            input_bytes=request_bytes,
        )
        if response.strip() != b"staged":
            raise GuestError("container did not confirm staged files")
        return {"ok": True, "file_count": len(files), "total_bytes": total}

    if operation == "execute":
        argv = payload.get("argv")
        timeout = payload.get("timeout_seconds")
        output_limit = payload.get("max_output_bytes")
        environment = payload.get("environment", {})
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
            or type(timeout) is not int
            or not 1 <= timeout <= int(state["timeout_seconds"])
            or type(output_limit) is not int
            or not 0 <= output_limit <= MAX_OUTPUT
            or not isinstance(environment, dict)
        ):
            raise GuestError("candidate process request is invalid")
        protected = {"PATH", "HOME"}
        if any(
            not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key)
            or key in protected
            or key.startswith(("DOCKER_", "AWS_", "PCB_", "SSH_"))
            for key in environment
        ):
            raise GuestError("candidate environment is invalid")
        env_args = [
            arg for key, value in sorted(environment.items()) for arg in ("--env", f"{key}={value}")
        ]
        started = time.monotonic()
        exit_code, stdout, stderr, timed_out = bounded_process(
            ["/usr/bin/docker", "exec", *env_args, container_id, *argv], timeout, output_limit
        )
        if timed_out:
            docker(["stop", "--time", "0", container_id])
        return {
            "ok": True,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout_b64": base64.b64encode(stdout).decode("ascii"),
            "stderr_b64": base64.b64encode(stderr).decode("ascii"),
        }

    if operation == "snapshot":
        validation = docker(
            [
                "exec",
                container_id,
                "python",
                "-I",
                "-B",
                "-S",
                "-c",
                "import os,stat,sys\n"
                "for base,dirs,files in os.walk('/workspace',followlinks=False):\n"
                " for name in dirs+files:\n"
                "  mode=os.lstat(os.path.join(base,name)).st_mode\n"
                "  if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)): sys.exit(41)\n"
                "print('ok')\n",
            ]
        )
        if validation.strip() != b"ok":
            raise GuestError("workspace validation failed")
        raw = docker(["cp", f"{container_id}:/workspace/.", "-"])
        if len(raw) > 512 * 1024**2:
            raise GuestError("workspace snapshot exceeds byte limit")
        entries: list[dict[str, object]] = []
        total = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES:
                raise GuestError("workspace snapshot exceeds file count limit")
            seen: set[str] = set()
            for member in members:
                safe_name(member.name)
                if member.name in seen:
                    raise GuestError("workspace snapshot contains duplicate paths")
                seen.add(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise GuestError("workspace snapshot contains link or special file")
                total += member.size
                snapshot_stream = archive.extractfile(member)
                if snapshot_stream is None:
                    raise GuestError("snapshot entry cannot be read")
                digest = hashlib.sha256(snapshot_stream.read()).hexdigest()
                entries.append(
                    {"path": member.name, "size_bytes": member.size, "digest": f"sha256:{digest}"}
                )
                if total > 512 * 1024**2:
                    raise GuestError("workspace snapshot exceeds byte limit")
        return {
            "ok": True,
            "files": entries,
            "archive_digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            "archive_b64": base64.b64encode(raw).decode("ascii"),
        }

    if operation == "attest":
        inspection = json.loads(docker(["inspect", container_id]))[0]
        config = inspection.get("Config", {})
        host = inspection.get("HostConfig", {})
        mounts = inspection.get("Mounts", [])
        tmpfs = host.get("Tmpfs", {})
        controls = {
            "network_none": host.get("NetworkMode") == "none",
            "private_namespaces": host.get("PidMode") != "host"
            and host.get("IpcMode") != "host"
            and host.get("UTSMode") != "host",
            "readonly_root": host.get("ReadonlyRootfs") is True,
            "drop_all_capabilities": "ALL" in host.get("CapDrop", []),
            "no_new_privileges": any(
                "no-new-privileges:true" in item for item in host.get("SecurityOpt", [])
            ),
            "nonroot": config.get("User") == "65532:65532",
            "bounded_memory": host.get("Memory") == state.get("memory_bytes")
            and host.get("MemorySwap") == state.get("memory_bytes"),
            "bounded_cpu": host.get("NanoCpus") == int(state.get("cpu_millis", 0)) * 1_000_000,
            "bounded_disk": "/workspace" in tmpfs
            and f"size={state.get('disk_bytes')}" in tmpfs["/workspace"],
            "bounded_pids": host.get("PidsLimit") == state.get("pids_limit"),
            "no_mounts_or_socket": not host.get("Binds")
            and not host.get("Devices")
            and not host.get("DeviceRequests")
            and all(item.get("Type") == "tmpfs" for item in mounts),
            "fresh_stage_fence": all(
                inspection.get("Config", {}).get("Labels", {}).get(f"pcb.{key}")
                == str(state.get(value))
                for key, value in (
                    ("sandbox", "sandbox_id"),
                    ("stage", "stage_id"),
                    ("fence", "fence"),
                )
            ),
        }
        if config.get("Image") != state.get("image") or any(
            not passed for passed in controls.values()
        ):
            raise GuestError("container isolation attestation failed")
        return {"ok": True, "controls": sorted(controls)}

    if operation == "terminate":
        docker(["stop", "--time", "0", container_id])
        return {"ok": True}
    raise GuestError("control operation is not allowlisted")


def main() -> int:
    try:
        body = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(body) > MAX_REQUEST:
            raise GuestError("request exceeds byte limit")
        request = cast(dict[str, Any], json.loads(body))
        if not isinstance(request, dict):
            raise GuestError("request must be an object")
        reply = handle(request)
        sys.stdout.write(json.dumps(reply, separators=(",", ":")))
        return 0
    except Exception:
        sys.stdout.write('{"ok":false,"error":"control operation rejected"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
