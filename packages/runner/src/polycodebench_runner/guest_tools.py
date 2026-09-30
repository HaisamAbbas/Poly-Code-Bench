"""Host-side client for the guest tool helper.

The host never builds a host shell command from model text. Every tool request is a JSON document
handed to the sandbox's own interpreter together with the helper source; the reply is one JSON
object that is parsed with a size bound. A reply that is missing or malformed means the guest is
unhealthy (infrastructure), not that the model erred.
"""

from __future__ import annotations

import ast
import base64
import io
import json
import tarfile
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from polycodebench_core.solve_contracts import RESERVED_PREFIX

from polycodebench_runner.contracts import (
    ExecRequest,
    InputManifest,
    SandboxHandle,
    WorkspaceManifest,
)
from polycodebench_runner.provider import SandboxError, SandboxProvider


def _compact(source: str) -> str:
    """Helper source without comments and docstrings, to keep the argument vector small."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


_SOURCE = _compact(Path(__file__).with_name("guest_helper.py").read_text(encoding="utf-8"))
# Shipped compressed in every call so it fits any host command line; the guest's own
# interpreter expands it, and no workspace file is involved in loading it.
GUEST_HELPER_SOURCE = (
    "import zlib,base64;exec(zlib.decompress(base64.b85decode('"
    + base64.b85encode(zlib.compress(_SOURCE.encode("utf-8"), 9)).decode("ascii")
    + "')).decode())"
)
MAX_ARGV_BYTES = 26_000  # below the Windows command-line limit of the docker client
INLINE_REQUEST_BYTES = 2_000
MAX_WORKSPACE_BYTES = 512 * 1024**2
MAX_WORKSPACE_FILES = 100_000
MAX_REPLY_BYTES = 6 * 1024 * 1024
EXEC_GRACE_SECONDS = 8
INBOX = ".pcb_inbox"


class GuestToolFailure(Exception):
    """The helper ran and reported a typed tool error (a model-visible result)."""

    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


class WorkspaceLimitExceeded(RuntimeError):
    """The model-built workspace is too large to freeze or checkpoint: a model-side condition."""


class GuestInfrastructureError(RuntimeError):
    """The guest could not run the helper: treat as an interrupted stage, never a model failure."""


class GuestToolbox:
    def __init__(
        self,
        provider: SandboxProvider,
        handle: SandboxHandle,
        *,
        operation_cap_seconds: int | None = None,
    ) -> None:
        cap = operation_cap_seconds
        if cap is None:
            cap = int(getattr(provider, "operation_timeout_seconds", 30))
        if cap <= EXEC_GRACE_SECONDS + 1:
            raise ValueError("sandbox operation timeout is too small to run guarded commands")
        self._provider = provider
        self._handle = handle
        self.max_command_seconds = cap - EXEC_GRACE_SECONDS

    async def invoke(
        self, op: str, fields: Mapping[str, Any] | None = None, *, timeout: int | None = None
    ) -> dict[str, Any]:
        request = json.dumps({"op": op, **(fields or {})}, separators=(",", ":"), sort_keys=True)
        if len(request.encode("utf-8")) > INLINE_REQUEST_BYTES:
            # Large requests travel as a staged file so the argument vector stays small.
            name = f"{INBOX}/{uuid4().hex}.req"
            data = request.encode("utf-8")
            await self._stage({name: data}, len(data) + 16)
            request = f"@file:{name}"
        argv = ("python", "-I", "-B", "-S", "-c", GUEST_HELPER_SOURCE, request)
        if sum(len(part.encode("utf-8")) for part in argv) > MAX_ARGV_BYTES:
            raise GuestInfrastructureError("guest argument vector would exceed its bound")
        wait = min(timeout if timeout is not None else 20, self.max_command_seconds)
        try:
            result = await self._provider.execute(
                self._handle,
                ExecRequest(
                    argv=argv,
                    timeout_seconds=wait + EXEC_GRACE_SECONDS,
                    max_output_bytes=MAX_REPLY_BYTES,
                ),
            )
        except (SandboxError, OSError) as error:
            raise GuestInfrastructureError("sandbox could not run the tool helper") from error
        if result.timed_out or result.exit_code != 0:
            raise GuestInfrastructureError(
                f"tool helper failed (exit {result.exit_code}, timed_out={result.timed_out})"
            )
        if len(result.stdout) >= MAX_REPLY_BYTES:
            raise GuestInfrastructureError("tool helper reply exceeded its size bound")
        try:
            reply = json.loads(result.stdout)
        except ValueError as error:
            raise GuestInfrastructureError("tool helper reply was not JSON") from error
        if not isinstance(reply, dict) or "ok" not in reply:
            raise GuestInfrastructureError("tool helper reply had no status")
        if reply["ok"] is not True:
            extra = {k: v for k, v in reply.items() if k not in {"ok", "error", "message"}}
            raise GuestToolFailure(
                str(reply.get("error", "tool_failure")), str(reply.get("message", "")), extra
            )
        return {k: v for k, v in reply.items() if k != "ok"}

    # ---- typed operations -----------------------------------------------------------

    async def list_files(self, path: str, depth: int, cursor: str | None) -> dict[str, Any]:
        return await self.invoke("list_files", {"path": path, "depth": depth, "cursor": cursor})

    async def read_file(self, path: str, start_line: int, max_lines: int) -> dict[str, Any]:
        return await self.invoke(
            "read_file", {"path": path, "start_line": start_line, "max_lines": max_lines}
        )

    async def search(
        self, pattern: str, regex: bool, path_glob: str, cursor: str | None
    ) -> dict[str, Any]:
        return await self.invoke(
            "search",
            {"pattern": pattern, "regex": regex, "path_glob": path_glob, "cursor": cursor},
            timeout=20,
        )

    async def apply_patch(
        self, diff: str, protected: list[str], max_file_bytes: int
    ) -> dict[str, Any]:
        name = f"{INBOX}/{uuid4().hex}.patch"
        data = diff.encode("utf-8")
        await self._stage({name: data}, len(data) + 16)
        return await self.invoke(
            "apply_patch",
            {"patch_file": name, "protected": protected, "max_file_bytes": max_file_bytes},
        )

    async def run_command(self, command: str, cwd: str, timeout_seconds: int) -> dict[str, Any]:
        seconds = min(timeout_seconds, self.max_command_seconds)
        return await self.invoke(
            "run_command",
            {"command": command, "cwd": cwd, "timeout_seconds": seconds},
            timeout=seconds,
        )

    async def run_argv(self, argv: list[str], cwd: str, timeout_seconds: int) -> dict[str, Any]:
        seconds = min(timeout_seconds, self.max_command_seconds)
        return await self.invoke(
            "run_argv",
            {"argv": argv, "cwd": cwd, "timeout_seconds": seconds},
            timeout=seconds,
        )

    async def sweep(self) -> int:
        return int((await self.invoke("sweep"))["killed"])

    async def process_count(self) -> list[int]:
        return [int(pid) for pid in (await self.invoke("process_count"))["processes"]]

    async def make_directories(self, directories: list[str]) -> None:
        if directories:
            await self.invoke("mkdirs", {"dirs": directories})

    async def restore_modes(self, modes: Mapping[str, int]) -> None:
        if modes:
            await self.invoke("restore_modes", {"modes": dict(modes)})

    async def stage_files(self, files: Mapping[str, bytes]) -> None:
        total = sum(len(data) for data in files.values())
        await self._stage(dict(files), total)

    async def snapshot(self) -> tuple[WorkspaceManifest, list[str]]:
        """Snapshot the workspace after deleting links and special files (recorded, not hidden).

        A workspace beyond the checkpoint limits raises ``WorkspaceLimitExceeded`` (the model
        built it); a guest that cannot be asked at all is infrastructure.
        """
        cleaned = await self.invoke("remove_unsafe")
        removed = [str(item) for item in cleaned["removed"]]
        if int(cleaned.get("removed_count", len(removed))) > len(removed):
            removed.append(f"... and {int(cleaned['removed_count']) - len(removed)} more")
        stats = await self.invoke("stats")
        if int(stats["bytes"]) > MAX_WORKSPACE_BYTES or int(stats["files"]) > MAX_WORKSPACE_FILES:
            raise WorkspaceLimitExceeded(
                f"workspace has {stats['files']} files and {stats['bytes']} bytes"
            )
        try:
            return await self._provider.snapshot(self._handle), removed
        except (SandboxError, OSError) as error:
            raise GuestInfrastructureError("workspace snapshot failed") from error

    async def _stage(self, files: Mapping[str, bytes], total: int) -> None:
        try:
            await self._provider.stage_inputs(
                self._handle, InputManifest(files=dict(files), max_total_bytes=max(total, 0))
            )
        except (SandboxError, OSError) as error:
            raise GuestInfrastructureError("workspace staging failed") from error


def extract_workspace(manifest: WorkspaceManifest) -> tuple[dict[str, bytes], dict[str, int]]:
    """Regular-file contents and permission bits from a snapshot; harness paths are excluded."""
    return extract_archive(manifest.archive_bytes)


def extract_directories(archive_bytes: bytes) -> list[str]:
    """Directory paths in a snapshot (including empty ones), harness paths excluded."""
    found = []
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
        for member in archive.getmembers():
            parts = member.name.split("/")
            if member.isdir() and not any(part.startswith(RESERVED_PREFIX) for part in parts):
                found.append(member.name)
    return sorted(found)


def extract_archive(archive_bytes: bytes) -> tuple[dict[str, bytes], dict[str, int]]:
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/")
            if any(part.startswith(RESERVED_PREFIX) for part in parts):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            files[member.name] = stream.read()
            modes[member.name] = member.mode & 0o777
    return files, modes
