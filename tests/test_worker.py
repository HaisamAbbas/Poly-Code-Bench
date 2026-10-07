"""Worker lifecycle regressions without a live database or sandbox driver."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.jobs import JobClaim, StageOutcome
from polycodebench_orchestration.worker import StageResult, WorkerService
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_runner.contracts import SandboxHandle, SandboxSpec
from polycodebench_runner.provider import SandboxProvider


def _claim() -> JobClaim:
    return JobClaim(
        job_id=uuid4(),
        execution_id=uuid4(),
        slot_id=uuid4(),
        worker_id=str(uuid4()),
        slot_key="slot-1",
        stage="solve",
        scope_type="attempt",
        scope_id=uuid4(),
        input_artifact_id=None,
        input_digest="sha256:" + "a" * 64,
        resource_class="small",
        queue_class="solve",
        fence=1,
        deliveries=1,
        lease_until_epoch=1_800_000_000,
    )


def _spec(claim: JobClaim) -> SandboxSpec:
    digest = "sha256:" + "a" * 64
    return SandboxSpec(
        stage_id="worker-test",
        fence=claim.fence,
        lane="solve",
        image="python@" + digest,
        image_digest=digest,
        cpu_millis=1000,
        memory_bytes=64 * 1024**2,
        disk_bytes=16 * 1024**2,
        pids_limit=32,
        timeout_seconds=10,
        ttl_seconds=300,
    )


class FakeRepository:
    def __init__(self, claim: JobClaim, events: list[str]) -> None:
        self.job_claim = claim
        self.next_claim: JobClaim | None = None
        self.events = events
        self.authority = True
        self.slot_state = "reserved"
        self.heartbeat_calls = 0
        self.dispatch_calls = 0
        self.dispatch_error_at: int | None = None
        self.heartbeat_error_at: int | None = None
        self.bind_error: Exception | None = None
        self.complete_error: Exception | None = None
        self.cleanup_error: Exception | None = None
        self.failure_class: str | None = None
        self.registration_active = True
        self.registration_heartbeats = 0
        self.last_claim_filter: tuple[UUID | None, UUID | None, str | None] | None = None

    def claim(
        self,
        worker_id: UUID,
        *,
        job_id: UUID | None = None,
        run_id: UUID | None = None,
        stage: str | None = None,
    ) -> JobClaim | None:
        assert worker_id == UUID(self.job_claim.worker_id)
        self.last_claim_filter = (job_id, run_id, stage)
        self.events.append("claim")
        claim = self.next_claim
        self.next_claim = None
        return claim

    def heartbeat_worker(self, worker_id: UUID) -> bool:
        assert worker_id == UUID(self.job_claim.worker_id)
        self.events.append("registration_heartbeat")
        self.registration_heartbeats += 1
        return self.registration_active

    def begin_dispatch(self, claim: JobClaim) -> None:
        assert claim == self.job_claim and self.authority
        self.events.append("begin_dispatch")
        self.slot_state = "busy"

    def bind_guest(self, claim: JobClaim, guest_id: str) -> None:
        assert claim == self.job_claim and guest_id == "guest-1"
        self.events.append("bind")
        if self.bind_error is not None:
            raise self.bind_error
        if not self.authority:
            raise LeaseLost()

    def dispatch_allowed(self, claim: JobClaim) -> bool:
        assert claim == self.job_claim
        self.dispatch_calls += 1
        if self.dispatch_error_at == self.dispatch_calls:
            raise RuntimeError("dispatch database unavailable")
        return self.authority

    def heartbeat(self, claim: JobClaim) -> bool:
        assert claim == self.job_claim
        self.events.append("heartbeat")
        self.heartbeat_calls += 1
        if self.heartbeat_error_at == self.heartbeat_calls:
            raise RuntimeError("heartbeat database unavailable")
        return self.authority

    def complete(self, claim: JobClaim, *, output_artifact_id: UUID, outcome: StageOutcome) -> str:
        assert claim == self.job_claim and output_artifact_id and outcome
        self.events.append("complete")
        if self.complete_error is not None:
            raise self.complete_error
        assert self.authority
        self.authority = False
        self.slot_state = "cleanup"
        return "succeeded"

    def fail(self, claim: JobClaim, *, failure_class: str, failure_code: str) -> str:
        assert claim == self.job_claim and self.authority and failure_code
        self.events.append("fail")
        self.failure_class = failure_class
        self.authority = False
        self.slot_state = "available" if self.slot_state == "reserved" else "cleanup"
        return "retry_wait"

    def confirm_slot_cleanup(
        self, claim: JobClaim, *, actor: str, destruction_verified: bool
    ) -> None:
        assert claim == self.job_claim and actor == claim.worker_id
        assert destruction_verified and not self.authority and self.slot_state == "cleanup"
        self.events.append("cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error
        self.slot_state = "available"


class FakeSandbox:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.create_started = asyncio.Event()
        self.create_gate = asyncio.Event()
        self.create_gate.set()
        self.destroy_started = asyncio.Event()
        self.destroy_gate = asyncio.Event()
        self.destroy_gate.set()
        self.create_error: Exception | None = None
        self.terminate_error: Exception | None = None
        self.destroy_error: Exception | None = None
        self.thread_gate: threading.Event | None = None
        self.terminated = asyncio.Event()
        self.handle: SandboxHandle | None = None

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.events.append("create")
        self.create_started.set()
        await self.create_gate.wait()
        if self.thread_gate is not None:
            assert await asyncio.to_thread(self.thread_gate.wait, 2)
        if self.create_error is not None:
            raise self.create_error
        self.handle = SandboxHandle(
            stage_id=spec.stage_id,
            fence=spec.fence,
            lane=spec.lane,
            driver="local_docker",
            resource_id="guest-1",
            expires_at_epoch=1_800_000_000,
            max_execution_seconds=spec.timeout_seconds,
            isolation_tier="development",
        )
        self.events.append("created")
        return self.handle

    async def terminate(self, handle: SandboxHandle, reason: str) -> None:
        assert handle == self.handle and reason
        self.events.append("terminate")
        self.terminated.set()
        if self.terminate_error is not None:
            raise self.terminate_error

    async def destroy(self, handle: SandboxHandle) -> None:
        assert handle == self.handle
        self.destroy_started.set()
        await self.destroy_gate.wait()
        if self.destroy_error is not None:
            raise self.destroy_error
        self.events.append("destroy")


class FakeExecutor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.error: Exception | None = None

    async def __call__(
        self,
        claim: JobClaim,
        handle: SandboxHandle,
        sandbox: SandboxProvider,
        artifacts: ArtifactRepository,
    ) -> StageResult:
        self.events.append("execute")
        self.started.set()
        try:
            await self.gate.wait()
            if self.error is not None:
                raise self.error
            return StageResult(uuid4(), StageOutcome(quality_gate="pass"))
        finally:
            self.finished.set()


def _worker() -> tuple[WorkerService, FakeRepository, FakeSandbox, FakeExecutor, list[str]]:
    claim = _claim()
    events: list[str] = []
    repository = FakeRepository(claim, events)
    sandbox = FakeSandbox(events)
    executor = FakeExecutor(events)
    worker = WorkerService(
        worker_id=UUID(claim.worker_id),
        repository=cast(PostgresJobRepository, repository),
        artifacts=cast(ArtifactRepository, object()),
        sandbox=cast(SandboxProvider, sandbox),
        spec_factory=_spec,
        executor=executor,
        heartbeat_seconds=1,
    )
    return worker, repository, sandbox, executor, events


def _fast(worker: WorkerService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "heartbeat_seconds", 0.02)
    monkeypatch.setattr(worker, "revocation_poll_seconds", 0.005)


async def _eventually(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=1)


def _run(scenario: Callable[[], Coroutine[Any, Any, None]]) -> None:
    async def checked() -> None:
        await asyncio.wait_for(scenario(), timeout=3)
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}, "worker leaked a child task"

    asyncio.run(checked())


def test_success_destroys_before_releasing_claim_slot() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        assert await worker.run_claim(repo.job_claim) == "succeeded"
        assert repo.slot_state == "available" and executor.finished.is_set()
        assert events.index("begin_dispatch") < events.index("heartbeat") < events.index("create")
        assert events.index("complete") < events.index("destroy") < events.index("cleanup")
        assert "fail" not in events and "terminate" not in events

    _run(scenario)


def test_foreign_worker_claim_is_rejected_before_repository_access() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        repo.job_claim = repo.job_claim.model_copy(update={"worker_id": str(uuid4())})
        with pytest.raises(LeaseLost, match="another worker"):
            await worker.run_claim(repo.job_claim)
        assert events == [] and repo.authority and repo.slot_state == "reserved"
        assert repo.dispatch_calls == 0 and not executor.started.is_set()

    _run(scenario)


def test_run_once_dispatches_one_claim_then_returns_idle() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        repo.next_claim = repo.job_claim
        assert await worker.run_once()
        assert not await worker.run_once()
        assert events.count("claim") == 2 and events.count("execute") == 1
        assert repo.slot_state == "available"

    _run(scenario)


def test_run_once_forwards_exact_job_and_stage_filters() -> None:
    async def scenario() -> None:
        worker, repo, _sandbox, _executor, _events = _worker()
        repo.next_claim = repo.job_claim
        requested_job = uuid4()
        assert await worker.run_once(job_id=requested_job, stage="solve")
        assert repo.last_claim_filter == (requested_job, None, "solve")

    _run(scenario)


def test_run_once_forwards_exact_run_and_stage_filters() -> None:
    async def scenario() -> None:
        worker, repo, _sandbox, _executor, _events = _worker()
        repo.next_claim = repo.job_claim
        requested_run = uuid4()
        assert await worker.run_once(run_id=requested_run, stage="solve")
        assert repo.last_claim_filter == (None, requested_run, "solve")

    _run(scenario)


def test_run_once_rejects_claim_owned_by_another_worker() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        repo.next_claim = repo.job_claim.model_copy(update={"worker_id": str(uuid4())})
        with pytest.raises(LeaseLost, match="another worker"):
            await worker.run_once()
        assert events == ["claim"] and repo.authority and repo.slot_state == "reserved"
        assert repo.dispatch_calls == 0 and not executor.started.is_set()

    _run(scenario)


def test_slow_create_keeps_lease_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        _fast(worker, monkeypatch)
        sandbox.create_gate.clear()
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await sandbox.create_started.wait()
        await _eventually(lambda: repo.heartbeat_calls >= 3)
        assert repo.slot_state == "busy" and not executor.started.is_set()
        sandbox.create_gate.set()
        assert await task == "succeeded"
        assert events.index("created") < events.index("execute")

    _run(scenario)


@pytest.mark.parametrize("check", ["heartbeat", "dispatch"])
def test_supervisor_database_error_revokes_execution(
    check: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        _fast(worker, monkeypatch)
        executor.gate.clear()
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await executor.started.wait()
        if check == "heartbeat":
            repo.heartbeat_error_at = repo.heartbeat_calls + 1
        else:
            repo.dispatch_error_at = repo.dispatch_calls + 1
        with pytest.raises(LeaseLost):
            await task
        assert executor.finished.is_set() and "complete" not in events
        assert events.index("terminate") < events.index("destroy") < events.index("fail")
        assert events.index("fail") < events.index("cleanup")
        assert repo.slot_state == "available"

    _run(scenario)


@pytest.mark.parametrize("revocation", ["scope", "heartbeat_error"])
def test_authority_loss_during_create_prevents_executor_dispatch(revocation: str) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        sandbox.create_gate.clear()
        if revocation == "heartbeat_error":
            repo.heartbeat_error_at = 1
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await sandbox.create_started.wait()
        if revocation == "scope":
            repo.authority = False
            repo.slot_state = "cleanup"
        sandbox.create_gate.set()
        with pytest.raises(LeaseLost):
            await task
        assert not executor.started.is_set() and "complete" not in events
        assert events.index("destroy") < events.index("cleanup")
        assert repo.slot_state == "available"

    _run(scenario)


@pytest.mark.parametrize("entry_point", ["run_claim", "run_once"])
def test_cancel_during_threaded_create_drains_handle_before_slot_release(
    monkeypatch: pytest.MonkeyPatch,
    entry_point: str,
) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        _fast(worker, monkeypatch)
        sandbox.thread_gate = threading.Event()
        sandbox.destroy_gate.clear()
        repo.next_claim = repo.job_claim
        task = asyncio.create_task(
            worker.run_once() if entry_point == "run_once" else worker.run_claim(repo.job_claim)
        )
        await sandbox.create_started.wait()
        task.cancel()
        await _eventually(lambda: repo.heartbeat_calls >= 2)
        assert not task.done() and repo.slot_state == "busy"
        task.cancel()
        sandbox.thread_gate.set()
        await sandbox.destroy_started.wait()
        assert not task.done() and "cleanup" not in events
        task.cancel()
        sandbox.destroy_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not executor.started.is_set()
        assert repo.failure_class == "worker_cancelled"
        assert events.index("created") < events.index("destroy") < events.index("fail")
        assert events.index("fail") < events.index("cleanup") and repo.slot_state == "available"

    _run(scenario)


def test_cancel_running_executor_drains_all_tasks() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        executor.gate.clear()
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await executor.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert executor.finished.is_set() and repo.slot_state == "available"
        assert "complete" not in events and "terminate" in events

    _run(scenario)


def test_cancellation_terminates_guest_before_waiting_for_executor_finalizer() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        terminated = sandbox.terminated

        async def execute(
            claim: JobClaim,
            handle: SandboxHandle,
            sandbox: SandboxProvider,
            artifacts: ArtifactRepository,
        ) -> StageResult:
            executor.started.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError("blocked executor returned")
            finally:
                await terminated.wait()
                executor.finished.set()

        worker.executor = execute
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await executor.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert executor.finished.is_set() and repo.slot_state == "available"

    _run(scenario)


def test_scope_revocation_stops_executor_without_failing_retired_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        _fast(worker, monkeypatch)
        executor.gate.clear()
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await executor.started.wait()
        repo.authority = False
        repo.slot_state = "cleanup"
        with pytest.raises(LeaseLost):
            await task
        assert executor.finished.is_set() and repo.slot_state == "available"
        assert "complete" not in events and "fail" not in events

    _run(scenario)


def test_cancel_during_successful_cleanup_waits_for_destruction() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        sandbox.destroy_gate.clear()
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await sandbox.destroy_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and repo.slot_state == "cleanup"
        sandbox.destroy_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo.slot_state == "available" and "complete" in events
        assert "fail" not in events

    _run(scenario)


@pytest.mark.parametrize("source", ["executor", "complete", "bind", "post_create_dispatch"])
def test_stage_failure_retires_lease_and_cleans_slot(source: str) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        error = RuntimeError("original stage failure")
        if source == "executor":
            executor.error = error
        elif source == "complete":
            repo.complete_error = error
        elif source == "bind":
            repo.bind_error = error
        else:
            repo.dispatch_error_at = 2
        with pytest.raises(RuntimeError):
            await worker.run_claim(repo.job_claim)
        assert events.index("destroy") < events.index("fail") < events.index("cleanup")
        assert repo.slot_state == "available"

    _run(scenario)


def test_failed_spec_releases_unprovisioned_reservation() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()

        def invalid_spec(claim: JobClaim) -> SandboxSpec:
            raise ValueError("invalid sandbox configuration")

        worker.spec_factory = invalid_spec
        with pytest.raises(ValueError, match="invalid sandbox"):
            await worker.run_claim(repo.job_claim)
        assert repo.slot_state == "available" and "fail" in events
        assert "begin_dispatch" not in events and "create" not in events
        assert "cleanup" not in events

    _run(scenario)


def test_create_failure_without_handle_keeps_uncertain_slot_in_cleanup() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        sandbox.create_error = RuntimeError("resource allocation status unknown")
        with pytest.raises(RuntimeError, match="status unknown"):
            await worker.run_claim(repo.job_claim)
        assert not repo.authority and repo.slot_state == "cleanup"
        assert not executor.started.is_set() and "cleanup" not in events
        assert "destroy" not in events and "fail" in events

    _run(scenario)


def test_cancelled_create_failure_keeps_uncertain_slot_reserved() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        sandbox.create_gate.clear()
        sandbox.create_error = RuntimeError("uncertain create failure")
        task = asyncio.create_task(worker.run_claim(repo.job_claim))
        await sandbox.create_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        sandbox.create_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo.slot_state == "cleanup" and repo.failure_class == "worker_cancelled"
        assert "cleanup" not in events and "destroy" not in events

    _run(scenario)


@pytest.mark.parametrize("primary_failure", [False, True])
def test_failed_destroy_never_releases_capacity(primary_failure: bool) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        sandbox.destroy_error = RuntimeError("destruction unverified")
        if primary_failure:
            executor.error = ValueError("executor failed")
        with pytest.raises(
            ValueError if primary_failure else RuntimeError,
            match="executor failed" if primary_failure else "destruction unverified",
        ):
            await worker.run_claim(repo.job_claim)
        assert repo.slot_state == "cleanup" and not repo.authority and "cleanup" not in events
        if primary_failure:
            assert "fail" in events

    _run(scenario)


def test_termination_failure_still_destroys_guest() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        executor.error = ValueError("executor failed")
        sandbox.terminate_error = RuntimeError("termination failed")
        with pytest.raises(ValueError, match="executor failed"):
            await worker.run_claim(repo.job_claim)
        assert repo.slot_state == "available"
        assert events.index("terminate") < events.index("destroy") < events.index("cleanup")

    _run(scenario)


@pytest.mark.parametrize("primary_error", [LeaseLost, ValueError])
def test_stale_cleanup_preserves_original_error(primary_error: type[Exception]) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        original = primary_error("original stage failure")
        executor.error = original
        repo.cleanup_error = LeaseLost("cleanup superseded")
        with pytest.raises(primary_error) as raised:
            await worker.run_claim(repo.job_claim)
        assert raised.value is original and "destroy" in events

    _run(scenario)


def test_idle_worker_renews_registration_before_every_claim() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_until_stopped(stop, idle_poll_seconds=0.005))
        await _eventually(lambda: repo.registration_heartbeats >= 3)
        stop.set()
        await task
        assert events == ["registration_heartbeat", "claim"] * repo.registration_heartbeats

    _run(scenario)


def test_run_scoped_watch_forwards_run_id_on_every_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        worker, repo, _sandbox, _executor, events = _worker()
        _fast(worker, monkeypatch)
        repo.next_claim = repo.job_claim
        stop = asyncio.Event()
        run_id = uuid4()
        task = asyncio.create_task(
            worker.run_until_stopped(stop, idle_poll_seconds=0.005, stage="solve", run_id=run_id)
        )
        await _eventually(lambda: "execute" in events)
        stop.set()
        await task
        assert repo.last_claim_filter == (None, run_id, "solve")

    _run(scenario)


def test_long_idle_poll_is_capped_to_registration_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        _fast(worker, monkeypatch)
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_until_stopped(stop, idle_poll_seconds=120))
        await _eventually(lambda: repo.registration_heartbeats >= 3)
        stop.set()
        await task
        assert not executor.started.is_set()

    _run(scenario)


def test_disabled_registration_prevents_new_claims() -> None:
    async def scenario() -> None:
        worker, repo, sandbox, executor, events = _worker()
        repo.registration_active = False
        await worker.run_until_stopped(asyncio.Event())
        assert events == ["registration_heartbeat"]

    _run(scenario)
