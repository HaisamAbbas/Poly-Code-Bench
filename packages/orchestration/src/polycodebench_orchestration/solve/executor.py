"""Worker stage executor: runs a solve session inside the guest the worker already provisioned.

The worker owns the sandbox lifecycle, the lease and the fence; this executor only runs the
controller. A redelivered job gets a fresh guest, so ``AgentSession`` restores the checkpointed
workspace into it. Model failures complete the stage (with a failed quality gate); infrastructure
interruptions raise so the scheduler retries the same work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from polycodebench_core.jobs import JobClaim, StageOutcome
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.solve_state import PostgresSolveRepository
from polycodebench_runner.contracts import SandboxHandle
from polycodebench_runner.guest_tools import GuestToolbox
from polycodebench_runner.provider import SandboxProvider

from polycodebench_orchestration.gateway.service import ModelGateway, ResponseStore
from polycodebench_orchestration.solve.session import (
    AgentSession,
    SingleShotSession,
    SolveResult,
)
from polycodebench_orchestration.solve.types import SolveAssignment
from polycodebench_orchestration.worker import StageResult


class AssignmentLoader(Protocol):
    def __call__(self, claim: JobClaim) -> SolveAssignment: ...


StoreFactory = Callable[[ArtifactRepository], ResponseStore]


def stage_outcome(result: SolveResult) -> StageOutcome:
    """Map a solve result to scheduler semantics. A wrong or missing answer is completed work."""
    if result.outcome.status == "model_failure":
        return StageOutcome(
            model_failure=True,
            quality_gate="fail",
            event_details={"reason": result.outcome.reason[:200]},
        )
    return StageOutcome(event_details={"reason": result.outcome.reason[:200]})


class SolveStageExecutor:
    """``StageExecutor`` implementation for the ``solve`` stage."""

    def __init__(
        self,
        *,
        load_assignment: AssignmentLoader,
        gateway: ModelGateway,
        jobs: PostgresJobRepository,
        solve_repository: PostgresSolveRepository,
        store_factory: StoreFactory,
    ) -> None:
        self._load = load_assignment
        self._gateway = gateway
        self._jobs = jobs
        self._repo = solve_repository
        self._store_factory = store_factory

    async def __call__(
        self,
        claim: JobClaim,
        handle: SandboxHandle | None,
        sandbox: SandboxProvider,
        artifacts: ArtifactRepository,
    ) -> StageResult:
        assignment = self._load(claim)
        store = self._store_factory(artifacts)
        common = {
            "assignment": assignment,
            "repository": self._repo,
            "store": store,
            "gateway": self._gateway,
            # Every commit and dispatch re-checks the lease and fence of this exact claim.
            "dispatch_allowed": lambda: self._jobs.dispatch_allowed(claim),
        }
        if assignment.effective.protocol.mode == "single_shot":
            result = await SingleShotSession(**common).run()  # type: ignore[arg-type]
        else:
            if handle is None:
                raise RuntimeError("agent execution requires a worker-managed guest")
            toolbox = GuestToolbox(sandbox, handle)
            result = await AgentSession(toolbox=toolbox, **common).run()
        return StageResult(
            output_artifact_id=result.output_artifact_id, outcome=stage_outcome(result)
        )


__all__ = ["AssignmentLoader", "SolveStageExecutor", "stage_outcome"]
