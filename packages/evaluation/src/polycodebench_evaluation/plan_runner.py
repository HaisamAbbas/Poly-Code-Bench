"""Supervisor-side execution of language-plugin plans through a SandboxProvider.

Plugins only describe work; this module is the trusted executor. It never interprets tool
output: it stages explicit inputs, runs the typed argument vector with a hard in-guest deadline
(so partial outputs survive a timeout), snapshots the workspace and hands the declared outputs
plus a supervisor-written execution record to the plugin's parser.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
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
from polycodebench_runner.contracts import ExecRequest, InputManifest, SandboxHandle, SandboxSpec
from polycodebench_runner.guest_tools import extract_archive
from polycodebench_runner.provider import SandboxProvider

DEADLINE_GRACE_SECONDS = 8
KILL_EXIT_CODES = frozenset({124, 137})
Lane = Literal["solve", "grading", "admission"]
PlanValidator = Callable[[ExecutionPlan], None]


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

    A candidate input is also accepted under a re-rooted path (``cand/solution.py`` /
    ``ref/solution.py``): the performance lane stages the same declared file twice, once per
    side, so both trees can be resolved from one candidate-role pool without the caller
    inventing role aliases.
    """
    files: dict[str, bytes] = {}
    for item in plan.inputs:
        pool = sources.get(item.role, {})
        key = item.path
        alt = key.removeprefix("work/") if item.role == "candidate" else key
        data = pool.get(key, pool.get(alt))
        if data is None and item.role == "candidate":
            # accept <root>/<relative path> for a re-rooted candidate input
            root, _, tail = key.partition("/")
            if tail:
                data = pool.get(tail)
        if data is None:
            raise PlanInputError(f"missing {item.role} input {item.path!r}")
        if item.digest is not None and sha256_bytes(data) != item.digest:
            raise PlanInputError(f"{item.role} input {item.path!r} does not match its digest")
        files[item.path] = data
    return files


class ReservedGuest:
    """One reserved worker guest: staged once, many *fresh processes* executed inside it.

    The performance lane needs exactly this shape (Technical Spec 13.2): candidate and reference
    run on the same physical worker, each iteration in its own process with equivalent
    initialization, while a normal plan run creates and destroys a guest per plan. Inputs are
    still resolved and digest-checked by :func:`materialize_inputs`, and every command still runs
    under the in-guest deadline, so a hung iteration cannot outlive its budget.
    """

    def __init__(self, runner: PlanRunner, handle: SandboxHandle, plan: ExecutionPlan) -> None:
        self._runner = runner
        self._handle = handle
        self._plan = plan
        self.commands: list[ExecutionRecord] = []

    @property
    def handle(self) -> SandboxHandle:
        return self._handle

    @property
    def sandbox_id(self) -> str:
        return self._handle.sandbox_id

    async def execute(
        self,
        plan: ExecutionPlan,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecutionRecord:
        self._runner._validate_plan(plan)
        if plan.image_digest != self._plan.image_digest:
            raise ValueError("reserved guest cannot execute a plan for another image")
        deadline = (
            timeout_seconds if timeout_seconds is not None else plan.resources.timeout_seconds
        )
        request = ExecRequest(
            argv=("timeout", "--signal=KILL", str(deadline), *argv),
            timeout_seconds=deadline + DEADLINE_GRACE_SECONDS,
            environment=dict(environment if environment is not None else plan.environment),
            max_output_bytes=plan.resources.max_output_bytes,
        )
        executed = await self._runner._provider.execute(self._handle, request)
        timed_out = executed.timed_out or (
            executed.exit_code in KILL_EXIT_CODES
            and executed.duration_ms >= int(deadline * 1000 * 0.9)
        )
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
        self.commands.append(record)
        return record

    async def read_files(self, paths: Sequence[str]) -> dict[str, bytes]:
        """Read specific workspace files with one typed guest command (base64, binary-safe).

        Cheaper and more precise than a full snapshot when only a few declared outputs matter -
        a performance iteration writes exactly one small record file.
        """
        if not paths:
            return {}
        script = (
            "import base64,json,sys\n"
            "out = {}\n"
            "for p in sys.argv[1:]:\n"
            "    try:\n"
            "        with open(p, 'rb') as h:\n"
            "            out[p] = base64.b64encode(h.read()).decode()\n"
            "    except OSError:\n"
            "        pass\n"
            "sys.stdout.write(json.dumps(out))\n"
        )
        record = await self.execute(self._plan, ("python", "-I", "-B", "-c", script, *paths))
        if record.exit_code != 0:
            return {}
        start = record.stdout_tail.find("{")
        if start == -1:
            return {}
        try:
            payload = json.loads(record.stdout_tail[start:])
        except ValueError:
            return {}
        files: dict[str, bytes] = {}
        for path, encoded in payload.items():
            try:
                files[path] = base64.b64decode(encoded)
            except (ValueError, TypeError):
                continue
        return files

    async def snapshot_outputs(self, paths: Sequence[str]) -> dict[str, bytes]:
        """Collect declared outputs after the last iteration (one archive read, not per run)."""
        snapshot = await self._runner._provider.snapshot(self._handle)
        archive, _ = extract_archive(snapshot.archive_bytes)
        collected: dict[str, bytes] = {}
        for path in paths:
            data = archive.get(path)
            if data is not None:
                collected[path] = data
        return collected


class PlanRunner:
    def __init__(
        self,
        provider: SandboxProvider,
        *,
        lane: Lane = "admission",
        first_fence: int = 0,
        plan_validator: PlanValidator | None = None,
    ) -> None:
        if first_fence < 0:
            raise ValueError("first fence must be non-negative")
        self._provider = provider
        self._lane = lane
        self._fence = first_fence
        self._plan_validator = plan_validator

    def _validate_plan(self, plan: ExecutionPlan) -> None:
        if self._plan_validator is not None:
            self._plan_validator(plan)

    @asynccontextmanager
    async def reserved_guest(
        self, plan: ExecutionPlan, files: Mapping[str, bytes], *, stage_id: str
    ) -> AsyncIterator[ReservedGuest]:
        """Reserve one guest for a sequence of executions on a single physical worker."""
        self._validate_plan(plan)
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
            yield ReservedGuest(self, handle, plan)
        finally:
            await self._provider.destroy(handle)

    async def run(
        self, plan: ExecutionPlan, files: Mapping[str, bytes], *, stage_id: str
    ) -> PlanRun:
        self._validate_plan(plan)
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
