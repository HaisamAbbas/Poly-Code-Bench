"""Disposable, isolated local service pairs for restore rehearsals (local-docker target).

An isolated environment is a fresh PostgreSQL + S3-compatible object store pair started from
the *same pinned image digests* as compose.yaml, on loopback-only ephemeral ports, with its
own anonymous volumes and a unique label. Nothing is shared with the source environment.
``teardown`` removes containers, volumes and the network and then proves nothing carrying the
rehearsal label remains (resources reclaimed).
"""

from __future__ import annotations

import json
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
LABEL = "org.polycodebench.rehearsal"


class DockerError(RuntimeError):
    pass


def docker(
    *arguments: str,
    input_bytes: bytes | None = None,
    input_file: BinaryIO | None = None,
    output_file: BinaryIO | None = None,
    timeout: int = 600,
) -> bytes:
    if input_bytes is not None and input_file is not None:
        raise ValueError("provide only one Docker stdin source")
    command = ["docker", *arguments]
    stdout = output_file if output_file is not None else subprocess.PIPE
    if input_file is None:
        result = subprocess.run(  # noqa: S603 - fixed docker argv
            command,
            input=input_bytes,
            stdout=stdout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    else:
        result = subprocess.run(  # noqa: S603 - fixed docker argv
            command,
            stdin=input_file,
            stdout=stdout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-2:]
        raise DockerError(f"docker {arguments[0]} failed: {' | '.join(tail)}")
    return result.stdout or b""


def pinned_images(compose_path: Path = REPO_ROOT / "compose.yaml") -> dict[str, str]:
    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
    images = {
        "postgres": services["postgres"]["image"],
        "object-store": services["object-store"]["image"],
    }
    for name, image in images.items():
        if "@sha256:" not in image:
            raise DockerError(f"{name} image in compose.yaml is not digest-pinned")
    return images


@dataclass
class IsolatedEnvironment:
    rehearsal_id: str
    network: str
    postgres_container: str
    object_store_container: str
    postgres_password: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    object_store_access_key: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    object_store_secret_key: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    postgres_port: int = 0
    object_store_port: int = 0
    containers: list[str] = field(default_factory=list)

    @property
    def database_url(self) -> str:
        encoded_password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://polycodebench:{encoded_password}@127.0.0.1:"
            f"{self.postgres_port}/polycodebench"
        )

    @property
    def object_store_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.object_store_port}"


def _port(container: str, private: str) -> int:
    mapping = docker("port", container, private).decode().strip().splitlines()[0]
    return int(mapping.rsplit(":", 1)[1])


def start(*, wait_seconds: int = 120) -> IsolatedEnvironment:
    images = pinned_images()
    rehearsal_id = uuid.uuid4().hex[:12]
    prefix = f"pcb-restore-{rehearsal_id}"
    env = IsolatedEnvironment(
        rehearsal_id=rehearsal_id,
        network=f"{prefix}-net",
        postgres_container=f"{prefix}-postgres",
        object_store_container=f"{prefix}-object-store",
    )
    label = f"{LABEL}={rehearsal_id}"
    docker("network", "create", "--label", label, env.network)
    try:
        docker(
            "run",
            "-d",
            "--name",
            env.postgres_container,
            "--label",
            label,
            "--network",
            env.network,
            "-e",
            "POSTGRES_DB=polycodebench",
            "-e",
            "POSTGRES_USER=polycodebench",
            "-e",
            f"POSTGRES_PASSWORD={env.postgres_password}",
            "-p",
            "127.0.0.1::5432",
            images["postgres"],
        )
        env.containers.append(env.postgres_container)
        docker(
            "run",
            "-d",
            "--name",
            env.object_store_container,
            "--label",
            label,
            "--network",
            env.network,
            "-e",
            f"AWS_ACCESS_KEY_ID={env.object_store_access_key}",
            "-e",
            f"AWS_SECRET_ACCESS_KEY={env.object_store_secret_key}",
            "-p",
            "127.0.0.1::8333",
            images["object-store"],
            "mini",
            "-dir=/data",
        )
        env.containers.append(env.object_store_container)
        env.postgres_port = _port(env.postgres_container, "5432/tcp")
        env.object_store_port = _port(env.object_store_container, "8333/tcp")
        _wait_ready(env, wait_seconds)
    except Exception:
        teardown(env)
        raise
    return env


def _wait_ready(env: IsolatedEnvironment, wait_seconds: int) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + wait_seconds
    postgres_ready = store_ready = False
    while time.monotonic() < deadline and not (postgres_ready and store_ready):
        if not postgres_ready:
            try:
                docker(
                    "exec",
                    env.postgres_container,
                    "pg_isready",
                    "-U",
                    "polycodebench",
                    "-d",
                    "polycodebench",
                    timeout=10,
                )
                # pg_isready succeeds during the init-time restart; require a real query.
                docker(
                    "exec",
                    env.postgres_container,
                    "psql",
                    "-U",
                    "polycodebench",
                    "-d",
                    "polycodebench",
                    "-tAc",
                    "SELECT 1",
                    timeout=10,
                )
                postgres_ready = True
            except DockerError:
                pass
        if not store_ready:
            try:
                urllib.request.urlopen(env.object_store_endpoint, timeout=3)  # noqa: S310
                store_ready = True
            except urllib.error.HTTPError:
                store_ready = True  # an S3 error document means the endpoint is serving
            except OSError:
                pass
        if not (postgres_ready and store_ready):
            time.sleep(1)
    if not (postgres_ready and store_ready):
        raise DockerError("isolated environment did not become ready")
    # The S3 gateway can accept connections before its filer is ready for bucket operations.
    time.sleep(3)


def teardown(env: IsolatedEnvironment) -> dict[str, object]:
    """Remove everything labelled for this rehearsal and prove it is gone."""

    label = f"{LABEL}={env.rehearsal_id}"
    containers = docker("ps", "-aq", "--filter", f"label={label}").decode().split()
    volumes: list[str] = []
    for container in containers:
        inspect = json.loads(docker("inspect", container))
        volumes.extend(
            mount["Name"] for mount in inspect[0].get("Mounts", []) if mount.get("Type") == "volume"
        )
    if containers:
        docker("rm", "-f", "-v", *containers)
    for volume in volumes:
        try:
            docker("volume", "rm", volume)
        except DockerError:
            pass  # removed with the container by -v
    try:
        docker("network", "rm", env.network)
    except DockerError:
        pass
    remaining_containers = docker("ps", "-aq", "--filter", f"label={label}").decode().split()
    remaining_networks = (
        docker("network", "ls", "-q", "--filter", f"label={label}").decode().split()
    )
    remaining_volumes = [
        volume
        for volume in volumes
        if docker("volume", "ls", "-q", "--filter", f"name={volume}").decode().split()
    ]
    return {
        "removed_containers": len(containers),
        "removed_volumes": len(volumes),
        "remaining_containers": len(remaining_containers),
        "remaining_networks": len(remaining_networks),
        "remaining_volumes": len(remaining_volumes),
        "reclaimed": not (remaining_containers or remaining_networks or remaining_volumes),
    }
