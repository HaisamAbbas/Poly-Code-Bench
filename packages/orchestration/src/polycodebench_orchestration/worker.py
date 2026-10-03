"""Worker lifecycle integrating durable leases, verified artifacts, and sandboxes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.jobs import JobClaim, StageOutcome
from polycodebench_core.telemetry import METRICS, log_context, safe_label
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.jobs import HEARTBEAT_SECONDS, PostgresJobRepository
from polycodebench_runner.contracts import (
    SandboxHandle,
    SandboxSpec,
)
from polycodebench_runner.provider import SandboxProvider


@dataclass(frozen=True, slots=True)
class StageResult:
    output_artifact_id: UUID
    outcome: StageOutcome


class StageExecutor(Protocol):
    async def __call__(
        self,
        claim: JobClaim,
        handle: SandboxHandle,
        sandbox: SandboxProvider,
        artifacts: ArtifactRepository,
    ) -> StageResult: ...


SpecFactory = Callable[[JobClaim], SandboxSpec]
LOGGER = logging.getLogger(__name__)


class WorkerService:
    """Claims once, supervises one guest, and never commits after lease revocation."""

    def __init__(
        self,
        *,
        worker_id: UUID,
        repository: PostgresJobRepository,
        artifacts: ArtifactRepository,
        sandbox: SandboxProvider,
        spec_factory: SpecFactory,
        executor: StageExecutor,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
        revocation_poll_seconds: int = 1,
    ) -> None:
        if heartbeat_seconds < 1 or revocation_poll_seconds < 1:
            raise ValueError("heartbeat and revocation polling intervals must be positive")
        self.worker_id = worker_id
        self.repository = repository
        self.artifacts = artifacts
        self.sandbox = sandbox
        self.spec_factory = spec_factory
        self.executor = executor
        self.heartbeat_seconds = heartbeat_seconds
        self.revocation_poll_seconds = revocation_poll_seconds

    async def run_once(self) -> bool:
        claim = self.repository.claim(self.worker_id)
        if claim is None:
            return False
        await self.run_claim(claim)
        return True

    async def run_until_stopped(
        self, stop: asyncio.Event, *, idle_poll_seconds: float = 1.0
    ) -> None:
        """Long-lived worker loop; stop prevents the next dispatch and drains current work."""
        if idle_poll_seconds <= 0:
            raise ValueError("idle polling interval must be positive")
        while not stop.is_set():
            if not self.repository.heartbeat_worker(self.worker_id):
                return
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=min(idle_poll_seconds, self.heartbeat_seconds, HEARTBEAT_SECONDS),
                )
            except TimeoutError:
                continue

    async def run_claim(self, claim: JobClaim) -> str:
        if claim.worker_id != str(self.worker_id):
            raise LeaseLost("claim belongs to another worker")
        handle: SandboxHandle | None = None
        creation_task: asyncio.Task[SandboxHandle] | None = None
        execution_task: asyncio.Task[StageResult] | None = None
        lost_task: asyncio.Task[bool] | None = None
        lease_lost = asyncio.Event()
        heartbeat_task: asyncio.Task[None] | None = None
        dispatch_started = False
        failure: BaseException | None = None
        try:
            if not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            spec = self.spec_factory(claim)
            self.repository.begin_dispatch(claim)
            dispatch_started = True
            heartbeat_task = asyncio.create_task(self._heartbeat(claim, lease_lost))
            # Providers may provision in a thread. Cancelling that await would discard
            # the resulting handle while the thread continues allocating a resource.
            creation_task = asyncio.create_task(self.sandbox.create(spec))
            handle = await asyncio.shield(creation_task)
            self.repository.bind_guest(claim, handle.resource_id)
            if lease_lost.is_set() or not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            execution_task = asyncio.create_task(
                self.executor(claim, handle, self.sandbox, self.artifacts)
            )
            lost_task = asyncio.create_task(lease_lost.wait())
            done, _pending = await asyncio.wait(
                {execution_task, lost_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if lost_task in done and lease_lost.is_set():
                raise LeaseLost()
            result = execution_task.result()
            if lease_lost.is_set() or not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            committed = self.repository.complete(
                claim,
                output_artifact_id=result.output_artifact_id,
                outcome=result.outcome,
            )
            METRICS.inc(
                "pcb_job_completions_total",
                queue_class=safe_label(claim.queue_class),
                outcome=safe_label(committed),
            )
            return committed
        except BaseException as error:
            failure = error
            if isinstance(error, LeaseLost):
                METRICS.inc(
                    "pcb_stale_commit_refusals_total", queue_class=safe_label(claim.queue_class)
                )
                with log_context(
                    job_id=claim.job_id, execution_id=claim.execution_id, fence=claim.fence
                ):
                    LOGGER.warning("lease lost; stage result will not be committed")
            raise
        finally:
            cleanup_task = asyncio.create_task(
                self._cleanup_claim(
                    claim,
                    handle=handle,
                    creation_task=creation_task,
                    children=(execution_task, lost_task, heartbeat_task),
                    dispatch_started=dispatch_started,
                    failure=failure,
                )
            )
            cancelled_during_cleanup = False
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError:
                    if cleanup_task.cancelled():
                        raise
                    # Repeated cancellation must also wait for verified destruction.
                    cancelled_during_cleanup = True
                except Exception:
                    if failure is None and not cancelled_during_cleanup:
                        raise
                    LOGGER.exception(
                        "worker cleanup failed job=%s fence=%s", claim.job_id, claim.fence
                    )
                    break
            if cancelled_during_cleanup:
                raise asyncio.CancelledError()

    async def _cleanup_claim(
        self,
        claim: JobClaim,
        *,
        handle: SandboxHandle | None,
        creation_task: asyncio.Task[SandboxHandle] | None,
        children: tuple[
            asyncio.Task[StageResult] | None, asyncio.Task[bool] | None, asyncio.Task[None] | None
        ],
        dispatch_started: bool,
        failure: BaseException | None,
    ) -> None:
        execution_task, lost_task, heartbeat_task = children
        for task in (execution_task, lost_task):
            if task is not None:
                task.cancel()
        if creation_task is not None:
            try:
                # Keep renewing the lease until the provisioning operation settles.
                handle = await creation_task
            except (Exception, asyncio.CancelledError):
                LOGGER.exception("sandbox creation failed without a handle job=%s", claim.job_id)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        destruction_verified = creation_task is None
        cleanup_error: Exception | None = None
        if handle is not None:
            if failure is not None:
                reason = (
                    "worker_cancelled"
                    if isinstance(failure, asyncio.CancelledError)
                    else "lease_lost"
                    if isinstance(failure, LeaseLost)
                    else "stage_infrastructure_failure"
                )
                try:
                    await self.sandbox.terminate(handle, reason)
                except Exception:
                    # Destruction still needs to run when graceful termination fails.
                    LOGGER.exception("sandbox termination failed job=%s", claim.job_id)
        await asyncio.gather(
            *(task for task in (execution_task, lost_task) if task is not None),
            return_exceptions=True,
        )
        if handle is not None:
            try:
                await self.sandbox.destroy(handle)
                destruction_verified = True
            except Exception as error:
                cleanup_error = error

        try:
            # Retire this delivery before making its slot available again, including
            # when authority checks failed temporarily rather than revoked the lease.
            if self.repository.dispatch_allowed(claim):
                failure_class = (
                    "worker_cancelled"
                    if isinstance(failure, asyncio.CancelledError)
                    else str(getattr(failure, "failure_class", None) or "worker_infrastructure")[
                        :64
                    ]
                )
                self.repository.fail(
                    claim,
                    failure_class=failure_class,
                    failure_code="worker_cancelled"
                    if isinstance(failure, asyncio.CancelledError)
                    else "stage_execution_failed",
                )
        except LeaseLost:
            pass  # A concurrent revocation already retired this delivery.
        except Exception as error:
            cleanup_error = cleanup_error or error
            destruction_verified = False

        if dispatch_started and destruction_verified:
            try:
                self.repository.confirm_slot_cleanup(
                    claim, actor=claim.worker_id, destruction_verified=True
                )
            except LeaseLost:
                if failure is None:
                    raise
                # A stale cleanup cannot touch a newer claim, or replace LeaseLost.
                LOGGER.info("slot cleanup was already superseded job=%s", claim.job_id)
        elif dispatch_started and handle is None:
            # Existing providers do not guarantee cleanup when create raises. An
            # unknown resource must retain its capacity reservation for recovery.
            LOGGER.error("slot retained pending verified provisioning cleanup job=%s", claim.job_id)
        if cleanup_error is not None:
            raise cleanup_error

    async def _heartbeat(self, claim: JobClaim, lease_lost: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time()
        try:
            while True:
                heartbeat_due = loop.time() >= next_heartbeat
                authority = (
                    self.repository.heartbeat(claim)
                    if heartbeat_due
                    else self.repository.dispatch_allowed(claim)
                )
                if not authority:
                    lease_lost.set()
                    return
                if heartbeat_due:
                    next_heartbeat = loop.time() + self.heartbeat_seconds
                await asyncio.sleep(min(self.revocation_poll_seconds, self.heartbeat_seconds))
        except Exception:
            lease_lost.set()
            LOGGER.exception(
                "worker authority check failed job=%s fence=%s", claim.job_id, claim.fence
            )
