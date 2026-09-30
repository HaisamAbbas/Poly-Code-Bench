"""Guest tool helper inside a real local Docker sandbox (opt-in: ``PCB_TEST_DOCKER=1``).

EVIDENCE LABEL: REAL DEVELOPMENT SANDBOX. Local Docker is development isolation only; these tests
prove tool behaviour, process cleanup and bounded output in a container, not production isolation.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from polycodebench_runner.contracts import SandboxHandle, SandboxSpec
from polycodebench_runner.guest_tools import (
    GuestInfrastructureError,
    GuestToolbox,
    GuestToolFailure,
    extract_directories,
    extract_workspace,
)
from polycodebench_runner.provider import LocalDockerSandboxProvider

IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1", reason="live Docker tools are opt-in"
)


def _spec() -> SandboxSpec:
    return SandboxSpec(
        stage_id=f"stage-{uuid4().hex[:8]}",
        fence=1,
        lane="solve",
        image=IMAGE,
        image_digest=DIGEST,
        cpu_millis=1000,
        memory_bytes=256 * 1024**2,
        disk_bytes=64 * 1024**2,
        pids_limit=128,
        timeout_seconds=120,
        ttl_seconds=900,
    )


def provider() -> LocalDockerSandboxProvider:
    return LocalDockerSandboxProvider(
        allowed_images={IMAGE: DIGEST},
        state_dir=Path.cwd() / ".cache" / f"solve-docker-{uuid4().hex}",
        provider_id="solve-tests",
        operation_timeout_seconds=90,
    )


async def with_toolbox(
    body: Callable[[GuestToolbox, LocalDockerSandboxProvider, SandboxHandle], object],
) -> object:
    sandbox = provider()
    handle = await sandbox.create(_spec())
    try:
        return await body(GuestToolbox(sandbox, handle), sandbox, handle)  # type: ignore[misc]
    finally:
        await sandbox.destroy(handle)


def run(body):  # type: ignore[no-untyped-def]
    return asyncio.run(with_toolbox(body))


def test_read_write_patch_search_list_roundtrip_in_the_guest() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.stage_files({"src/app.py": b"def f():\n    return 1\n", "README.md": b"hi\n"})
        listing = await tools.list_files(".", 2, None)
        assert [e["path"] for e in listing["entries"]] == ["README.md", "src", "src/app.py"]
        page = await tools.read_file("src/app.py", 1, 400)
        assert page["text"] == "1\tdef f():\n2\t    return 1"
        diff = (
            "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n"
            " def f():\n-    return 1\n+    return 2\n"
        )
        applied = await tools.apply_patch(diff, [], 1_000_000)
        assert applied["files"][0]["action"] == "modify"
        assert "return 2" in (await tools.read_file("src/app.py", 1, 10))["text"]
        found = await tools.search("return", False, "*", None)
        assert [m["path"] for m in found["matches"]] == ["src/app.py"]
        with pytest.raises(GuestToolFailure) as protected:
            await tools.apply_patch(diff.replace("return 2", "return 3"), ["src"], 1_000_000)
        assert protected.value.code == "protected_path"
        # the harness inbox never shows up in listings
        assert not any(
            ".pcb_" in e["path"] for e in (await tools.list_files(".", 5, None))["entries"]
        )

    run(body)


def test_run_command_reports_exit_output_resources_and_a_minimal_environment() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        ok = await tools.run_command("echo out; echo err >&2; pwd; exit 3", ".", 20)
        assert ok["exit_code"] == 3 and ok["timed_out"] is False
        assert ok["stdout"].split() == ["out", "/workspace"] and ok["stderr"].strip() == "err"
        assert ok["elapsed_ms"] >= 0 and ok["max_rss_kb"] > 0
        env = await tools.run_command("env | sort", ".", 20)
        assert "PATH=" in env["stdout"]
        for forbidden in ("AWS_", "PCB_", "DOCKER_", "SSH_"):
            assert forbidden not in env["stdout"]
        net = await tools.run_command(
            "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 2)\"", ".", 20
        )
        assert net["exit_code"] != 0  # no network inside the guest
        await tools.stage_files({"sub/x.txt": b"x"})
        where = await tools.run_command("pwd", "sub", 20)
        assert where["stdout"].strip() == "/workspace/sub"
        with pytest.raises(GuestToolFailure) as escape:
            await tools.run_command("pwd", "../..", 20)
        assert escape.value.code == "path_forbidden"

    run(body)


def test_large_output_is_bounded_but_counted() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        result = await tools.run_command(
            "python -c \"print('x' * 1000); \" ; yes y | head -c 3000000", ".", 30
        )
        assert result["stdout_bytes"] > 3_000_000
        assert len(result["stdout"].encode()) < 300_000 and "bytes omitted" in result["stdout"]

    run(body)


def test_timeout_kills_the_process_group_and_the_sandbox_survives() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        slow = await tools.run_command("echo begun > marker.txt; sleep 60", ".", 2)
        assert slow["timed_out"] is True and slow["exit_code"] is None
        assert await tools.process_count() == []
        assert (await tools.read_file("marker.txt", 1, 5))["text"] == "1\tbegun"

    run(body)


def test_descendants_are_stopped_when_a_command_ends_and_no_zombies_accumulate() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        detach = (
            "sleep 300 & "
            "setsid nohup sleep 300 >/dev/null 2>&1 & "
            'python -c "import os,time\nif os.fork()==0:\n os.setsid()\n'
            ' if os.fork()==0: time.sleep(300)\n os._exit(0)"'
        )
        first = await tools.run_command(detach + "; echo launched", ".", 20)
        assert "launched" in first["stdout"] and first["descendants_killed"] >= 1
        assert await tools.process_count() == []
        for _ in range(25):  # each leaves orphans; none may pile up against the PID limit
            await tools.run_command("sleep 100 & setsid sleep 100 & true", ".", 20)
        assert await tools.process_count() == []
        census = await tools.run_command("ls /proc | grep -c '^[0-9]'", ".", 20)
        assert int(census["stdout"].strip()) < 12

    run(body)


def test_candidate_files_cannot_hijack_the_helper() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.run_command(
            "printf 'raise SystemExit(99)\\n' > json.py; cp json.py re.py; cp json.py os.py;"
            " cp json.py sitecustomize.py; mkdir -p .pcb_inbox; ls",
            ".",
            20,
        )
        listing = await tools.list_files(".", 1, None)
        assert {"json.py", "re.py", "os.py"} <= {e["path"] for e in listing["entries"]}
        assert (await tools.read_file("json.py", 1, 5))["text"].startswith("1\traise")

    run(body)


def test_pathological_regex_times_out_without_stalling_the_sandbox() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.stage_files({"haystack.txt": b"a" * 60 + b"b\n"})
        with pytest.raises(GuestToolFailure) as slow:
            await tools.search("(a+)+$", True, "*", None)
        assert slow.value.code == "search_timeout"
        with pytest.raises(GuestToolFailure) as invalid:
            await tools.search("(unclosed", True, "*", None)
        assert invalid.value.code == "regex_invalid"
        assert (await tools.search("aaa", False, "*", None))["matches"]

    run(body)


def test_links_created_by_commands_are_removed_before_a_snapshot() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.stage_files({"real.txt": b"data\n"})
        await tools.run_command(
            "ln -s real.txt alias.txt; ln -s /etc/passwd outside.txt; mkfifo pipe", ".", 20
        )
        with pytest.raises(GuestToolFailure) as link:
            await tools.read_file("alias.txt", 1, 5)
        assert link.value.code == "path_forbidden"
        manifest, removed = await tools.snapshot()
        assert removed == ["alias.txt", "outside.txt", "pipe"]
        files, _ = extract_workspace(manifest)
        assert set(files) == {"real.txt"}

    run(body)


def test_snapshot_restores_bytes_and_permission_bits_into_a_fresh_sandbox() -> None:
    async def first(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.run_command(
            "printf '#!/bin/sh\\necho ran\\n' > go.sh; chmod 755 go.sh; echo d > data.txt", ".", 20
        )
        await tools.stage_files({"seed.txt": b"s"})
        await tools.apply_patch(
            "--- /dev/null\n+++ b/patched.txt\n@@ -0,0 +1 @@\n+p\n", [], 1_000_000
        )
        manifest, _ = await tools.snapshot()
        return extract_workspace(manifest)

    files, modes = run(first)  # type: ignore[misc]
    assert set(files) == {"go.sh", "data.txt", "seed.txt", "patched.txt"}
    assert modes["go.sh"] & 0o111

    async def second(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.stage_files(files)
        await tools.restore_modes(modes)
        # Docker mounts tmpfs noexec, so scripts run through their interpreter; what matters
        # here is that the permission bits and writability survived the round trip.
        ran = await tools.run_command(
            "stat -c %a go.sh; sh go.sh && echo more >> data.txt && cat data.txt", ".", 20
        )
        assert ran["exit_code"] == 0 and ran["stdout"].split() == ["755", "ran", "d", "more"]

    run(second)


def test_a_dead_sandbox_is_infrastructure_not_a_tool_error() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await sandbox.terminate(handle, "test_interrupt")
        with pytest.raises(GuestInfrastructureError):
            await tools.read_file("x.txt", 1, 5)

    run(body)


def test_large_requests_are_staged_and_still_validated_by_the_guest() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        with pytest.raises(GuestToolFailure):
            await tools.invoke("list_files", {"path": "x" * 70_000})

    run(body)


def test_empty_directories_and_hostile_workspaces_survive_snapshot_and_restore() -> None:
    async def body(tools, sandbox, handle):  # type: ignore[no-untyped-def]
        await tools.stage_files({"src/a.py": b"x\n"})
        await tools.make_directories(["build/cache", "src"])
        ran = await tools.run_command(
            "chmod 000 src/a.py; ln -s a.py src/link; mkfifo src/pipe; echo ok", ".", 20
        )
        assert ran["exit_code"] == 0
        manifest, removed = await tools.snapshot()
        assert sorted(removed) == ["src/link", "src/pipe"]
        files, _modes = extract_workspace(manifest)
        assert files == {"src/a.py": b"x\n"}  # unreadable mode was repaired, links removed
        assert "build/cache" in extract_directories(manifest.archive_bytes)

    run(body)
