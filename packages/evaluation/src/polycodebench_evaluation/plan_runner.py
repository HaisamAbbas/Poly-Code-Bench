"""Supervisor-side execution of language-plugin plans through a SandboxProvider.

Plugins only describe work; this module is the trusted executor. It never interprets tool
output: it stages explicit inputs, runs the typed argument vector with a hard in-guest deadline
(so partial outputs survive a timeout), snapshots the workspace and hands the declared outputs
plus a supervisor-written execution record to the plugin's parser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_plugins_api import (
    EXECUTION_RECORD_PATH,
    DictArtifactReader,
    ExecutionPlan,
    ExecutionRecord,
    make_record,
    record_bytes,
)
from polycodebench_runner.contracts import ExecRequest, InputManifest, SandboxSpec
from polycodebench_runner.guest_tools import extract_archive
from polycodebench_runner.provider import SandboxProvider

DEADLINE_GRACE_SECONDS = 8
KILL_EXIT_CODES = frozenset({124, 137})
Lane = Literal["solve", "grading", "admission"]


class PlanInputError(ValueError):
    """A plan input is missing or does not match its declared digest."""


@dataclass(slots=True)
class PlanRun:
    plan: ExecutionPlan
    record: ExecutionRecord
    outputs: dict[str, bytes] = field(default_factory=dict)

    def reader(self) -> DictArtifactReader:
        return DictArtifactReader(
            {**self.outputs, EXECUTION_RECORD_PATH: record_bytes(self.record)}
        )


def materialize_inputs(
    plan: ExecutionPlan, sources: Mapping[str, Mapping[str, bytes]]
) -> dict[str, bytes]:
    """Resolve each declared input from its role's trusted source and verify declared digests.

    ``sources`` maps a role (candidate/overlay/baseline/config) to ``{path: bytes}``. A candidate
    can therefore never supply an ``overlay`` or ``config`` input, whatever its file names.
    """
    files: dict[str, bytes] = {}
    for item in plan.inputs:
        pool = sources.get(item.role, {})
        key = item.path
        alt = key.removeprefix("work/") if item.role == "candidate" else key
        data = pool.get(key, pool.get(alt))
        if data is None:
            raise PlanInputError(f"missing {item.role} input {item.path!r}")
        if item.digest is not None and sha256_bytes(data) != item.digest:
            raise PlanInputError(f"{item.role} input {item.path!r} does not match its digest")
        files[item.path] = data
    return files


class PlanRunner:
    def __init__(self, provider: SandboxProvider, *, lane: Lane = "admission") -> None:
        self._provider = provider
        self._lane = lane
        self._fence = 0

    async def run(
        self, plan: ExecutionPlan, files: Mapping[str, bytes], *, stage_id: str
    ) -> PlanRun:
        self._fence += 1
        res = plan.resources
        spec = SandboxSpec(
            stage_id=stage_id,
            fence=self._fence,
            lane=self._lane,
            image=plan.image,
            image_digest=plan.image_digest,
            cpu_millis=res.cpu_millis,
            memory_bytes=res.memory_bytes,
            disk_bytes=res.disk_bytes,
            pids_limit=res.pids_limit,
            executable_workspace=res.executable_workspace,
            timeout_seconds=res.timeout_seconds + DEADLINE_GRACE_SECONDS + 2,
            ttl_seconds=res.timeout_seconds + 120,
        )
        handle = await self._provider.create(spec)
        try:
            await self._provider.stage_inputs(
                handle, InputManifest(files=dict(files), max_total_bytes=res.disk_bytes)
            )
            deadline = res.timeout_seconds
            # The in-guest deadline kills only the plan's process tree and leaves the guest
            # alive, so partial outputs (for example which case was running) can be collected.
            argv = ("timeout", "--signal=KILL", str(deadline), *plan.argv)
            executed = await self._provider.execute(
                handle,
                ExecRequest(
                    argv=argv,
                    timeout_seconds=deadline + DEADLINE_GRACE_SECONDS,
                    environment=dict(plan.environment),
                    max_output_bytes=res.max_output_bytes,
                ),
            )
            timed_out = executed.timed_out or (
                executed.exit_code in KILL_EXIT_CODES
                and executed.duration_ms >= int(deadline * 1000 * 0.9)
            )
            outputs: dict[str, bytes] = {}
            if not executed.timed_out:
                snapshot = await self._provider.snapshot(handle)
                archive, _ = extract_archive(snapshot.archive_bytes)
                for declared in plan.outputs:
                    data = archive.get(declared.path)
                    if data is not None:
                        outputs[declared.path] = data[: declared.max_bytes]
            record = make_record(
                plan,
                exit_code=None if timed_out else executed.exit_code,
                timed_out=timed_out,
                duration_ms=executed.duration_ms,
                stdout=executed.stdout,
                stderr=executed.stderr,
                isolation_tier=executed.isolation_tier,
                sandbox_id=executed.sandbox_id,
            )
            return PlanRun(plan=plan, record=record, outputs=outputs)
        finally:
            await self._provider.destroy(handle)


def digest_files(files: Mapping[str, bytes]) -> str:
    """Stable digest of a path -> bytes mapping (used for candidate and bundle identities)."""
    return str(canonical_digest({path: sha256_bytes(data) for path, data in sorted(files.items())}))
