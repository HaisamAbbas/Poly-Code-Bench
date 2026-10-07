"""Worker lifecycle integrating durable leases, verified artifacts, and sandboxes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.jobs import JobClaim, StageOutcome
from polycodebench_core.telemetry import METRICS, log_context, safe_label
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.jobs import HEARTBEAT_SECONDS, PostgresJobRepository
from polycodebench_runner.contracts import (
    ExecRequest,
    ExecResult,
    InputManifest,
    SandboxHandle,
    SandboxSpec,
    WorkspaceManifest,
)
from polycodebench_runner.provider import SandboxProvider


@dataclass(frozen=True, slots=True)
class StageResult:
    output_artifact_id: UUID
    outcome: StageOutcome
    evaluation_gate: Literal["pass", "fail", "unknown"] | None = None


class StageExecutor(Protocol):
    async def __call__(
        self,
        claim: JobClaim,
        handle: SandboxHandle | None,
        sandbox: SandboxProvider,
        artifacts: ArtifactRepository,
    ) -> StageResult: ...


SpecFactory = Callable[[JobClaim], SandboxSpec]
LOGGER = logging.getLogger(__name__)


class _ClaimScopedSandboxProvider:
    """Bind each plan guest to the claimed capacity slot until destruction is verified."""

    def __init__(
        self, provider: SandboxProvider, repository: PostgresJobRepository, claim: JobClaim
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._claim = claim
        self.active_handle: SandboxHandle | None = None
        self._destroyed_but_bound = False
        self.unknown_resource = False

    @property
    def cleanup_verified(self) -> bool:
        return self.active_handle is None and not self.unknown_resource

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        if self.active_handle is not None:
            raise RuntimeError("a stage may own only one plan guest at a time")
        if not self._repository.dispatch_allowed(self._claim):
            raise LeaseLost()
        creation = asyncio.create_task(self._provider.create(spec))
        try:
            handle = await asyncio.shield(creation)
        except asyncio.CancelledError:
            try:
                handle = await creation
            except BaseException:
                self.unknown_resource = True
                raise
            await self._attach_or_destroy(handle)
            try:
                await self.terminate(handle, "worker_cancelled")
            finally:
                await self.destroy(handle)
            raise
        except BaseException:
            self.unknown_resource = True
            raise
        await self._attach_or_destroy(handle)
        return handle

    async def _attach_or_destroy(self, handle: SandboxHandle) -> None:
        try:
            self._repository.bind_guest(self._claim, handle.resource_id)
        except BaseException:
            try:
                await self._provider.terminate(handle, "guest_registration_failed")
                await self._provider.destroy(handle)
            except BaseException:
                self.active_handle = handle
                self.unknown_resource = True
            raise
        self.active_handle = handle
        self._destroyed_but_bound = False

    def _assert_active(self, handle: SandboxHandle) -> None:
        if self.active_handle != handle:
            raise LeaseLost("plan guest is not bound to this worker delivery")

    async def stage_inputs(self, handle: SandboxHandle, manifest: InputManifest) -> None:
        self._assert_active(handle)
        await self._provider.stage_inputs(handle, manifest)

    async def execute(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        self._assert_active(handle)
        return await self._provider.execute(handle, request)

    async def snapshot(self, handle: SandboxHandle) -> WorkspaceManifest:
        self._assert_active(handle)
        return await self._provider.snapshot(handle)

    async def terminate(self, handle: SandboxHandle, reason: str) -> None:
        self._assert_active(handle)
        await self._provider.terminate(handle, reason)

    async def destroy(self, handle: SandboxHandle) -> None:
        self._assert_active(handle)
        if not self._destroyed_but_bound:
            await self._provider.destroy(handle)
            self._destroyed_but_bound = True
        self._repository.unbind_guest(self._claim, handle.resource_id)
        self.active_handle = None
        self._destroyed_but_bound = False

    async def cleanup_active(self, reason: str) -> None:
        handle = self.active_handle
        if handle is None:
            return
        if not self._destroyed_but_bound:
            try:
                await self.terminate(handle, reason)
            finally:
                await self.destroy(handle)
        else:
            await self.destroy(handle)


class WorkerService:
    """Claims once, supervises one guest, and never commits after lease revocation."""

    def __init__(
        self,
        *,
        worker_id: UUID,
        repository: PostgresJobRepository,
        artifacts: ArtifactRepository,
        sandbox: SandboxProvider,
        spec_factory: SpecFactory | None,
        executor: StageExecutor,
        executor_manages_sandbox: bool = False,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
        revocation_poll_seconds: int = 1,
    ) -> None:
        if heartbeat_seconds < 1 or revocation_poll_seconds < 1:
            raise ValueError("heartbeat and revocation polling intervals must be positive")
        if not executor_manages_sandbox and spec_factory is None:
            raise ValueError("a worker-managed sandbox requires a sandbox spec factory")
        self.worker_id = worker_id
        self.repository = repository
        self.artifacts = artifacts
        self.sandbox = sandbox
        self.spec_factory = spec_factory
        self.executor = executor
        self.executor_manages_sandbox = executor_manages_sandbox
        self.heartbeat_seconds = heartbeat_seconds
        self.revocation_poll_seconds = revocation_poll_seconds

    async def run_once(
        self,
        *,
        job_id: UUID | None = None,
        run_id: UUID | None = None,
        stage: str | None = None,
    ) -> bool:
        claim = self.repository.claim(self.worker_id, job_id=job_id, run_id=run_id, stage=stage)
        if claim is None:
            return False
        await self.run_claim(claim)
        return True

    async def run_until_stopped(
        self,
        stop: asyncio.Event,
        *,
        idle_poll_seconds: float = 1.0,
        stage: str | None = None,
        run_id: UUID | None = None,
    ) -> None:
        """Long-lived worker loop; stop prevents the next dispatch and drains current work."""
        if idle_poll_seconds <= 0:
            raise ValueError("idle polling interval must be positive")
        while not stop.is_set():
            if not self.repository.heartbeat_worker(self.worker_id):
                return
            if await self.run_once(stage=stage, run_id=run_id):
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
        managed_sandbox: _ClaimScopedSandboxProvider | None = None
        try:
            if not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            if self.executor_manages_sandbox:
                spec = None
            else:
                assert self.spec_factory is not None
                spec = self.spec_factory(claim)
            self.repository.begin_dispatch(claim)
            dispatch_started = True
            heartbeat_task = asyncio.create_task(self._heartbeat(claim, lease_lost))
            sandbox_provider = self.sandbox
            if self.executor_manages_sandbox:
                managed_sandbox = _ClaimScopedSandboxProvider(
                    self.sandbox, self.repository, claim
                )
                sandbox_provider = managed_sandbox
            if spec is not None:
                # Providers may provision in a thread. Cancelling that await would discard
                # the resulting handle while the thread continues allocating a resource.
                creation_task = asyncio.create_task(self.sandbox.create(spec))
                handle = await asyncio.shield(creation_task)
                self.repository.bind_guest(claim, handle.resource_id)
            if lease_lost.is_set() or not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            execution_task = asyncio.create_task(
                self.executor(claim, handle, sandbox_provider, self.artifacts)
            )
            lost_task = asyncio.create_task(lease_lost.wait())
            done, _pending = await asyncio.wait(
                {execution_task, lost_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if lost_task in done and lease_lost.is_set():
                raise LeaseLost()
            result = execution_task.result()
            if managed_sandbox is not None and not managed_sandbox.cleanup_verified:
                raise RuntimeError("stage executor returned before verified guest cleanup")
            if lease_lost.is_set() or not self.repository.dispatch_allowed(claim):
                raise LeaseLost()
            if result.evaluation_gate is None:
                committed = self.repository.complete(
                    claim,
                    output_artifact_id=result.output_artifact_id,
                    outcome=result.outcome,
                )
            else:
                committed = self.repository.complete(
                    claim,
                    output_artifact_id=result.output_artifact_id,
                    outcome=result.outcome,
                    evaluation_gate=result.evaluation_gate,
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
                    managed_sandbox=managed_sandbox,
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
        managed_sandbox: _ClaimScopedSandboxProvider | None,
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

        # A plan-managed executor owns any guests it creates through the provider. Its clean
        # return confirms that every plan's context manager destroyed its guest. On exceptions
        # we cannot know whether a nested provider left an unknown resource, so keep the slot
        # reserved for the scheduler's verified recovery path.
        destruction_verified = creation_task is None and (
            not self.executor_manages_sandbox or failure is None
        )
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
        if managed_sandbox is not None:
            reason = (
                "worker_cancelled"
                if isinstance(failure, asyncio.CancelledError)
                else "lease_lost"
                if isinstance(failure, LeaseLost)
                else "stage_execution_failed"
            )
            try:
                await managed_sandbox.cleanup_active(reason)
            except Exception as error:
                cleanup_error = cleanup_error or error
                LOGGER.exception("plan guest cleanup failed job=%s", claim.job_id)
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
                    **(
                        {"sandbox_cleanup_verified": True}
                        if managed_sandbox is not None and managed_sandbox.cleanup_verified
                        else {}
                    ),
                )
        except LeaseLost:
            pass  # A concurrent revocation already retired this delivery.
        except Exception as error:
            cleanup_error = cleanup_error or error
            destruction_verified = False

        if dispatch_started and destruction_verified and managed_sandbox is None:
            try:
                self.repository.confirm_slot_cleanup(
                    claim, actor=claim.worker_id, destruction_verified=True
                )
            except LeaseLost:
                if failure is None:
                    raise
                # A stale cleanup cannot touch a newer claim, or replace LeaseLost.
                LOGGER.info("slot cleanup was already superseded job=%s", claim.job_id)
        elif dispatch_started and handle is None and creation_task is not None:
            # Existing providers do not guarantee cleanup when create raises. An
            # unknown resource must retain its capacity reservation for recovery.
            LOGGER.error("slot retained pending verified provisioning cleanup job=%s", claim.job_id)
        elif (
            dispatch_started
            and managed_sandbox is not None
            and not managed_sandbox.cleanup_verified
        ):
            LOGGER.error(
                "slot retained pending executor-managed sandbox cleanup job=%s", claim.job_id
            )
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
