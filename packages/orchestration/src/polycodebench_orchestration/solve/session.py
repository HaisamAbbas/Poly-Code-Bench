"""Solve sessions: the single-shot and standard-agent controllers.

The controller runs outside the candidate guest. Everything it needs to survive a crash is a
committed event plus the checkpoint written with it, so a fresh process (and a fresh sandbox)
can continue exactly where the last commit left off:

* a model response that was recorded by the gateway but not yet committed is consumed from the
  gateway's stored response for the same logical turn key, never re-requested;
* a tool call without a committed result is the only thing that may run again, after the
  workspace has been restored from the checkpoint it was issued against, and the repetition is
  recorded;
* a checkpoint whose workspace, transcript or protocol do not belong together is refused.

Model failures (invalid or wrong output, exhausted budgets) become a :class:`SolveOutcome`.
Infrastructure failures raise :class:`SolveInterrupted` and never produce one.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.model_contracts import (
    BudgetExhausted,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderCallFailed,
    TextBlock,
    ToolCallBlock,
    stable_json_bytes,
)
from polycodebench_core.solve_context import (
    CompletedTurn,
    build_context,
    render_budget,
)
from polycodebench_core.solve_contracts import (
    BudgetState,
    CheckpointMismatch,
    ContextBudgetExceeded,
    SolveError,
    SolveInterrupted,
    SolveOutcome,
    ToolErrorCode,
    ToolResult,
    tool_specs,
)
from polycodebench_core.solve_extraction import (
    Extraction,
    extract_from_response,
    freeze_workspace_files,
    protected_changes,
    workspace_patch,
)
from polycodebench_core.solve_prompts import system_prompt, task_message
from polycodebench_persistence.solve_state import (
    CheckpointRow,
    NewCheckpoint,
    NewEvent,
    PostgresSolveRepository,
)
from polycodebench_runner.guest_tools import (
    GuestInfrastructureError,
    GuestToolbox,
    WorkspaceLimitExceeded,
    extract_archive,
    extract_directories,
    extract_workspace,
)

from polycodebench_orchestration.gateway.service import GatewayResult, ModelGateway, ResponseStore
from polycodebench_orchestration.solve.tools import ToolRunner
from polycodebench_orchestration.solve.types import SolveAssignment

MUTATING_MARKED = frozenset({"apply_patch", "run_command", "run_public_tests"})
WORKSPACE_FILE_MODE = 0o644
TERMINAL_KINDS = frozenset({"candidate_frozen", "model_failure"})
BUDGET_ERROR_DIMENSIONS = {"tool_calls", "active_solve_seconds"}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def empty_workspace_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT):
        pass
    return buffer.getvalue()


@dataclass(frozen=True)
class SolveResult:
    outcome: SolveOutcome
    output_artifact_id: UUID  # the candidate artifact, or the final transcript manifest
    candidate_payload_digest: str | None
    last_event_seq: int


class _ModelFailure(Exception):
    """The model's own actions made the session unable to continue (recorded, not retried)."""


class SolveConfigurationError(RuntimeError):
    """The sandbox cannot honour the protocol; fail closed before any model call."""


@dataclass
class _Workspace:
    artifact_id: UUID
    digest: str
    modes: dict[str, int] = field(default_factory=dict)


@dataclass
class _EventRef:
    seq: int
    kind: str
    artifact_id: str
    digest: str


@dataclass
class _Turn:
    index: int
    response: ModelResponse
    results: list[ToolResult] = field(default_factory=list)
    started: set[str] = field(default_factory=set)

    @property
    def calls(self) -> tuple[ToolCallBlock, ...]:
        return self.response.tool_calls

    @property
    def pending(self) -> list[ToolCallBlock]:
        done = {result.tool_call_id for result in self.results}
        return [call for call in self.calls if call.call_id not in done]


@dataclass
class _State:
    last_seq: int = -1
    budget: BudgetState = field(default_factory=BudgetState)
    turns: list[_Turn] = field(default_factory=list)
    refs: list[_EventRef] = field(default_factory=list)
    workspace: _Workspace | None = None
    terminal: str | None = None
    terminal_payload: dict[str, Any] = field(default_factory=dict)
    exhausted: str | None = None
    tool_budget_hit: bool = False
    recoveries: int = 0


def apply_event_budget(budget: BudgetState, kind: str, payload: dict[str, Any]) -> BudgetState:
    """Budget consumed by one committed event. Used live and when rebuilding from events, so the
    two can never disagree."""
    if kind == "model_turn":
        return budget.plus(
            turns=1,
            input_tokens=int(payload["charged_input_tokens"]),
            output_tokens=int(payload["charged_output_tokens"]),
            active_ms=int(payload["elapsed_ms"]),
        )
    if kind == "tool_result":
        result = payload["result"]
        elapsed = int(result["elapsed_ms"])
        invalid = result["error_code"] in {
            ToolErrorCode.INVALID_ARGUMENTS,
            ToolErrorCode.UNKNOWN_TOOL,
            ToolErrorCode.TOOL_NOT_ALLOWED,
        }
        return budget.plus(
            tool_calls=1,
            invalid_tool_calls=1 if invalid else 0,
            active_ms=elapsed,
            repeated_compute_ms=elapsed if payload.get("replayed") else 0,
        )
    return budget


class _SessionBase:
    """State, commits, restoration and freezing shared by both protocols."""

    def __init__(
        self,
        *,
        assignment: SolveAssignment,
        repository: PostgresSolveRepository,
        store: ResponseStore,
        gateway: ModelGateway,
        dispatch_allowed: Callable[[], bool] | None = None,
        forbidden_markers: tuple[bytes, ...] = (),
    ) -> None:
        self.a = assignment
        self.repo = repository
        self.store = store
        self.gateway = gateway
        self.dispatch_allowed = dispatch_allowed
        self.forbidden_markers = (*assignment.forbidden_markers, *forbidden_markers)
        self.effective = assignment.effective
        self.state = _State()

    # ------------------------------------------------------------------ persistence

    def _put_json(self, payload: dict[str, Any], kind: str) -> tuple[UUID, str]:
        data = stable_json_bytes(payload)
        return self.store.put(data, kind=kind, media_type="application/json"), _sha(data)

    def _transcript_manifest(
        self, refs: list[_EventRef], pending: list[str], workspace: _Workspace, budget: BudgetState
    ) -> dict[str, Any]:
        return {
            "kind": "solve_transcript_manifest",
            "attempt_id": str(self.a.attempt_id),
            "protocol_digest": self.effective.digest,
            "last_seq": refs[-1].seq,
            "events": [
                {"seq": r.seq, "kind": r.kind, "artifact_id": r.artifact_id, "digest": r.digest}
                for r in refs
            ],
            "pending_call_ids": pending,
            "budget": budget.model_dump(),
            "workspace_digest": workspace.digest,
            "workspace_artifact_id": str(workspace.artifact_id),
            "workspace_modes": workspace.modes,
        }

    def _commit(
        self,
        events: list[tuple[str, dict[str, Any]]],
        *,
        workspace: _Workspace,
        pending: list[str],
    ) -> list[int]:
        """Persist events, then the checkpoint that covers them, in one database transaction.

        Artifacts are uploaded first (content-addressed, harmless if the commit never happens);
        the sequence compare-and-swap then decides whether this controller still owns the
        transcript.
        """
        state = self.state
        budget = state.budget
        new_refs: list[_EventRef] = []
        rows: list[NewEvent] = []
        for offset, (kind, payload) in enumerate(events, start=1):
            artifact_id, digest = self._put_json(payload, f"solve_event_{kind}")
            new_refs.append(_EventRef(state.last_seq + offset, kind, str(artifact_id), digest))
            rows.append(NewEvent(kind, artifact_id))
            budget = apply_event_budget(budget, kind, payload)
        refs = [*state.refs, *new_refs]
        manifest = self._transcript_manifest(refs, pending, workspace, budget)
        manifest_id, manifest_digest = self._put_json(manifest, "solve_transcript_manifest")
        checkpoint = NewCheckpoint(
            workspace_manifest_id=workspace.artifact_id,
            transcript_manifest_id=manifest_id,
            workspace_digest=workspace.digest,
            transcript_digest=manifest_digest,
            protocol_digest=self.effective.digest,
            accumulated_budget=budget.model_dump(),
            pending_call_ids=pending,
        )
        sequences = self.repo.commit(
            self.a.attempt_id,
            expected_last_seq=state.last_seq,
            events=rows,
            checkpoint=checkpoint,
            dispatch_allowed=self.dispatch_allowed,
        )
        state.last_seq = sequences[-1]
        state.refs = refs
        state.budget = budget
        state.workspace = workspace
        self._last_manifest_id = manifest_id
        return sequences

    _last_manifest_id: UUID | None = None

    def _new_workspace(self, archive: bytes, modes: dict[str, int]) -> _Workspace:
        artifact_id = self.store.put(
            archive, kind="solve_workspace", media_type="application/x-tar"
        )
        return _Workspace(artifact_id, _sha(archive), modes)

    # ---------------------------------------------------------------------- restore

    def _restore(self, checkpoint: CheckpointRow) -> _Workspace:
        """Rebuild state from a checkpoint after proving its parts belong together."""
        if checkpoint.protocol_digest != self.effective.digest:
            raise CheckpointMismatch("checkpoint was written under a different protocol")
        raw = self.store.get(checkpoint.transcript_manifest_id)
        if _sha(raw) != checkpoint.transcript_digest:
            raise CheckpointMismatch("transcript manifest bytes do not match the checkpoint")
        manifest = json.loads(raw)
        if (
            manifest["protocol_digest"] != checkpoint.protocol_digest
            or manifest["workspace_digest"] != checkpoint.workspace_digest
            or manifest["last_seq"] != checkpoint.event_seq
            or manifest["workspace_artifact_id"] != str(checkpoint.workspace_manifest_id)
            or manifest["attempt_id"] != str(self.a.attempt_id)
        ):
            raise CheckpointMismatch("workspace and transcript do not describe the same state")
        archive = self.store.get(checkpoint.workspace_manifest_id)
        if _sha(archive) != checkpoint.workspace_digest:
            raise CheckpointMismatch("workspace archive bytes do not match the checkpoint")
        rows = self.repo.events(self.a.attempt_id, upto_seq=checkpoint.event_seq)
        if [(r.event_seq, r.kind, str(r.payload_artifact_id)) for r in rows] != [
            (e["seq"], e["kind"], e["artifact_id"]) for e in manifest["events"]
        ]:
            raise CheckpointMismatch("committed events differ from the transcript manifest")
        state = _State(last_seq=checkpoint.event_seq)
        turns: dict[int, _Turn] = {}
        budget = BudgetState()
        for entry in manifest["events"]:
            payload = json.loads(self.store.get(UUID(entry["artifact_id"])))
            if _sha(stable_json_bytes(payload)) != entry["digest"]:
                raise CheckpointMismatch("an event payload does not match its recorded digest")
            state.refs.append(
                _EventRef(entry["seq"], entry["kind"], entry["artifact_id"], entry["digest"])
            )
            budget = apply_event_budget(budget, entry["kind"], payload)
            self._absorb(state, turns, entry["kind"], payload)
        if budget.model_dump() != checkpoint.accumulated_budget:
            raise CheckpointMismatch("accumulated budget does not match the committed events")
        state.budget = budget
        state.turns = [turns[index] for index in sorted(turns)]
        workspace = _Workspace(
            checkpoint.workspace_manifest_id,
            checkpoint.workspace_digest,
            {k: int(v) for k, v in manifest["workspace_modes"].items()},
        )
        state.workspace = workspace
        self.state = state
        self._last_manifest_id = checkpoint.transcript_manifest_id
        return workspace

    @staticmethod
    def _absorb(state: _State, turns: dict[int, _Turn], kind: str, payload: dict[str, Any]) -> None:
        if kind == "model_turn":
            response = ModelResponse.model_validate(payload["response"], strict=False)
            turns[int(payload["turn_index"])] = _Turn(int(payload["turn_index"]), response)
        elif kind == "tool_started":
            turns[int(payload["turn_index"])].started.add(payload["tool_call_id"])
        elif kind == "tool_result":
            turn = turns[int(payload["turn_index"])]
            result = ToolResult.model_validate(payload["result"], strict=False)
            turn.results.append(result)
            if result.error_code == ToolErrorCode.BUDGET_EXHAUSTED:
                state.tool_budget_hit = True
        elif kind == "recovery":
            state.recoveries += 1
        elif kind == "budget_exhausted":
            state.exhausted = str(payload["dimension"])
        elif kind in TERMINAL_KINDS:
            state.terminal = kind
            state.terminal_payload = payload

    # ----------------------------------------------------------------------- prompts

    def _public_group_ids(self) -> list[str]:
        return sorted(self.a.public_groups)

    def _task_message(self) -> str:
        return task_message(
            effective=self.effective,
            instructions=self.a.instructions,
            contract=self.a.contract,
            required_outputs=self.a.required_outputs,
            protected_paths=[
                path
                for path in self.a.protected_paths
                if any(
                    item == path or item.startswith(path.rstrip("/") + "/")
                    for item in self.a.visible_files
                )
            ],
            public_test_groups=self._public_group_ids(),
        )

    def _budget_text(self) -> str:
        budget = self.effective.budget
        return render_budget(
            self.state.budget.remaining(budget),
            {
                "model_turns": budget.model_turns,
                "tool_calls": budget.tool_calls,
                "input_tokens": budget.input_tokens,
                "output_tokens": budget.output_tokens,
                "active_seconds": budget.active_solve_seconds,
            },
        )

    def _request_template(self) -> dict[str, Any]:
        config = self.a.config
        return {
            "temperature": config.temperature,
            "seed": self.gateway.provider_seed(config, self.a.sample_seed),
            "reasoning": config.reasoning,
        }

    def _guard_request(self, request: ModelRequest) -> None:
        """Fail closed if any declared-hidden marker would reach the model."""
        body = request.canonical_bytes()
        for marker in self.forbidden_markers:
            if marker and marker in body:
                raise SolveError("hidden task material would have reached the model")

    # -------------------------------------------------------------------- model call

    async def _call_model(
        self, request: ModelRequest, turn_index: int
    ) -> tuple[GatewayResult, int]:
        self._guard_request(request)
        started = time.monotonic()
        try:
            result = await self.gateway.call(
                scope=self.a.scope,
                logical_call_key=f"turn-{turn_index}",
                config=self.a.config,
                config_document_id=self.a.config_document_id,
                protocol=self.effective.definition(),
                request=request,
                dispatch_allowed=self.dispatch_allowed,
            )
        except ProviderCallFailed as error:
            raise SolveInterrupted(
                f"provider call failed ({error.failure.kind.value}:{error.failure.code})"
            ) from error
        return result, int((time.monotonic() - started) * 1000)

    def _turn_payload(
        self,
        turn_index: int,
        result: GatewayResult,
        elapsed_ms: int,
        max_output: int,
        estimated_input: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        usage = result.response.usage
        return {
            "turn_index": turn_index,
            "intent_id": str(result.intent_id),
            "source": result.source,
            "delivery_index": result.delivery_index,
            "settlement_state": result.settlement_state,
            "response": result.response.model_dump(mode="json"),
            "request": {
                "max_output_tokens": max_output,
                "estimated_input_tokens": estimated_input,
                "token_counter": self.effective.protocol.context.token_counter,
            },
            "context": context,
            "usage": usage.model_dump(mode="json"),
            # Unknown usage is charged conservatively, never as zero.
            "charged_input_tokens": usage.input_tokens
            if usage.input_tokens is not None
            else estimated_input,
            "charged_output_tokens": usage.output_tokens
            if usage.output_tokens is not None
            else max_output,
            "usage_basis": "reported" if usage.complete else "conservative_estimate",
            "elapsed_ms": elapsed_ms,
            "dropped_controls": list(result.dropped_controls),
        }

    # ---------------------------------------------------------------------- freezing

    def _terminal_result(
        self, outcome: SolveOutcome, candidate_artifact: UUID | None, digest: str | None
    ) -> SolveResult:
        output = candidate_artifact or self._last_manifest_id
        assert output is not None
        return SolveResult(outcome, output, digest, self.state.last_seq)

    def _stored_terminal(self) -> SolveResult | None:
        """A session that already reached a terminal event returns the same result again."""
        if self.state.terminal is None:
            return None
        candidate = self.repo.candidate_for(self.a.attempt_id)
        payload = self.state.terminal_payload
        outcome = SolveOutcome.model_validate(payload["outcome"], strict=False)
        assert self._last_manifest_id is not None
        return SolveResult(
            outcome,
            candidate.canonical_artifact_id if candidate else self._last_manifest_id,
            candidate.payload_digest if candidate else None,
            self.state.last_seq,
        )

    def _freeze_extraction(
        self,
        extraction: Extraction,
        *,
        workspace: _Workspace,
        reason_prefix: str,
        budget_dimension: str | None = None,
    ) -> SolveResult:
        """Store the candidate exactly as extracted, then commit the terminal event."""
        content = extraction.content_bytes()
        artifact_id = self.store.put(content, kind="candidate", media_type="application/json")
        digest = extraction.digest()
        evaluable = extraction.validity == "valid" or (
            extraction.submission_kind == "structured_findings"
            and "patch_digest" in extraction.payload
        )
        row_payload = {
            **extraction.summary(),
            "protocol_digest": self.effective.protocol_digest,
            "effective_protocol_digest": self.effective.digest,
            "base_digest": self.a.base_digest,
            "attempt_id": str(self.a.attempt_id),
            "budget_exhausted": budget_dimension,
        }
        row = self.repo.freeze_candidate(
            self.a.attempt_id,
            payload_digest=digest,
            submission_kind=extraction.submission_kind,
            payload=row_payload,
            canonical_artifact_id=artifact_id,
            expected_last_seq=self.state.last_seq,
            dispatch_allowed=self.dispatch_allowed,
        )
        artifact_id = row.canonical_artifact_id
        if extraction.validity == "valid":
            status: Literal["candidate_frozen", "model_failure"] = "candidate_frozen"
            reason = reason_prefix
        else:
            status = "candidate_frozen" if evaluable else "model_failure"
            reason = f"{reason_prefix}:contract_invalid:" + ",".join(extraction.reasons)
        outcome = SolveOutcome(
            status=status,
            reason=reason,
            validity=extraction.validity,
            candidate_digest=digest,
            candidate_artifact_id=str(artifact_id),
            budget_exhausted=budget_dimension,
            frozen_after_exhaustion=budget_dimension is not None and status == "candidate_frozen",
            detail={"reasons": list(extraction.reasons), "ignored": list(extraction.ignored)},
        )
        kind = "candidate_frozen" if status == "candidate_frozen" else "model_failure"
        self._commit(
            [(kind, {"outcome": outcome.model_dump(mode="json")})],
            workspace=workspace,
            pending=[],
        )
        return self._terminal_result(outcome, artifact_id, digest)

    def _record_failure(
        self, reason: str, workspace: _Workspace, *, dimension: str | None = None
    ) -> SolveResult:
        outcome = SolveOutcome(
            status="model_failure",
            reason=reason,
            validity="none",
            budget_exhausted=dimension,
        )
        self._commit(
            [("model_failure", {"outcome": outcome.model_dump(mode="json")})],
            workspace=workspace,
            pending=[],
        )
        return self._terminal_result(outcome, None, None)


# ===================================================================== single shot


class SingleShotSession(_SessionBase):
    """One request, no tools, no feedback; the declared extraction rule decides the candidate."""

    async def run(self) -> SolveResult:
        checkpoint = self.repo.latest_checkpoint(self.a.attempt_id)
        if checkpoint is not None:
            workspace = self._restore(checkpoint)
            stored = self._stored_terminal()
            if stored is not None:
                return stored
        else:
            archive = empty_workspace_archive()
            workspace = self._new_workspace(archive, {})
            self._commit(
                [("session_started", self._started_payload())], workspace=workspace, pending=[]
            )
        if not self.state.turns:
            failed = await self._turn(workspace)
            if failed is not None:
                return failed
        turn = self.state.turns[0]
        rule = self.effective.protocol.single_shot_extraction
        assert rule is not None
        extraction = extract_from_response(
            turn.response.text,
            contract=self.a.contract,
            rule=rule,
            required_outputs=self.a.required_outputs,
        )
        return self._freeze_extraction(extraction, workspace=workspace, reason_prefix="single_shot")

    def _started_payload(self) -> dict[str, Any]:
        return {
            "protocol_digest": self.effective.protocol_digest,
            "effective_protocol_digest": self.effective.digest,
            "mode": "single_shot",
            "tools": [],
            "base_digest": self.a.base_digest,
            "budget": self.effective.budget.model_dump(),
        }

    async def _turn(self, workspace: _Workspace) -> SolveResult | None:
        self.gateway.check_compatibility(
            self.a.config,
            self.effective.definition(),
            requires_structured_output=False,
        )
        budget = self.effective.budget
        max_output = min(self.a.config.max_output_tokens, budget.output_tokens)
        request = ModelRequest(
            system=system_prompt(self.effective),
            messages=(Message(role="user", blocks=(TextBlock(text=self._task_message()),)),),
            max_output_tokens=max_output,
            **self._request_template(),
        )
        estimated = -(-len(request.canonical_bytes()) // 4)
        if estimated > self.effective.protocol.context.max_input_context_tokens:
            # Single-shot cannot truncate: the planner rejects oversized tasks (spec 8.4).
            return self._record_failure(
                "context_budget_failure", workspace, dimension="context_budget"
            )
        result, elapsed = await self._call_model(request, 0)
        payload = self._turn_payload(
            0, result, elapsed, max_output, estimated, {"estimated_tokens": estimated}
        )
        self._commit([("model_turn", payload)], workspace=workspace, pending=[])
        self.state.turns.append(_Turn(0, result.response))
        return None


# ========================================================================== agent


class AgentSession(_SessionBase):
    """The standard agent protocol (Technical Spec 9.1)."""

    def __init__(self, *, toolbox: GuestToolbox, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.toolbox = toolbox
        budget = self.effective.budget
        assert budget.per_command_seconds is not None
        if toolbox.max_command_seconds < budget.per_command_seconds:
            raise SolveConfigurationError(
                f"sandbox caps commands at {toolbox.max_command_seconds}s but the protocol allows "
                f"{budget.per_command_seconds}s"
            )
        self.runner = ToolRunner(
            toolbox,
            self.a,
            self.store,
            tools=self.effective.tools,
            per_command_seconds=budget.per_command_seconds,
        )

    async def run(self) -> SolveResult:
        try:
            return await self._run()
        except _ModelFailure as failure:
            workspace = self.state.workspace
            assert workspace is not None
            return self._record_failure(str(failure), workspace)
        except GuestInfrastructureError as error:
            raise SolveInterrupted(f"sandbox failure: {error}") from error

    async def _run(self) -> SolveResult:
        checkpoint = self.repo.latest_checkpoint(self.a.attempt_id)
        if checkpoint is None:
            workspace = await self._start()
        else:
            workspace = self._restore(checkpoint)
            stored = self._stored_terminal()
            if stored is not None:
                return stored
            workspace = await self._reprovision(checkpoint, workspace)
        while True:
            state = self.state
            turn = state.turns[-1] if state.turns else None
            if turn is not None and turn.pending:
                workspace = await self._run_pending(turn, workspace)
                continue
            if turn is not None and not turn.calls:
                return await self._freeze_workspace("final_response", workspace)
            if state.exhausted is not None:
                return await self._freeze_workspace(
                    "budget_exhausted", workspace, dimension=state.exhausted
                )
            dimension = None
            if state.tool_budget_hit:
                dimension = "tool_calls"
            else:
                dimension = state.budget.exhausted(self.effective.budget)
            if dimension is not None:
                self._commit(
                    [("budget_exhausted", {"dimension": dimension})],
                    workspace=workspace,
                    pending=[],
                )
                state.exhausted = dimension
                continue
            outcome = await self._model_turn(workspace)
            if outcome == "exhausted":
                continue

    # ----------------------------------------------------------------- start/resume

    async def _start(self) -> _Workspace:
        files = dict(self.a.visible_files)
        await self.toolbox.stage_files(files)
        await self.toolbox.restore_modes({path: WORKSPACE_FILE_MODE for path in files})
        manifest, _ = await self.toolbox.snapshot()
        restored, modes = extract_workspace(manifest)
        workspace = self._new_workspace(manifest.archive_bytes, modes)
        self._commit(
            [
                (
                    "session_started",
                    {
                        "protocol_digest": self.effective.protocol_digest,
                        "effective_protocol_digest": self.effective.digest,
                        "mode": "standard_agent",
                        "tools": self.effective.tools,
                        "base_digest": self.a.base_digest,
                        "budget": self.effective.budget.model_dump(),
                        "workspace_files": len(restored),
                    },
                )
            ],
            workspace=workspace,
            pending=[],
        )
        return workspace

    async def _reprovision(self, checkpoint: CheckpointRow, workspace: _Workspace) -> _Workspace:
        """Put the checkpointed workspace into the (fresh) sandbox and record the recovery."""
        archive = self.store.get(workspace.artifact_id)
        files = extract_archive(archive)[0]
        await self.toolbox.stage_files(files)
        await self.toolbox.make_directories(extract_directories(archive))
        await self.toolbox.restore_modes(workspace.modes)
        pending = self.state.turns[-1].pending if self.state.turns else []
        replay = [call.call_id for call in pending if call.call_id in self.state.turns[-1].started]
        self._commit(
            [
                (
                    "recovery",
                    {
                        "restored_from_seq": checkpoint.event_seq,
                        "workspace_digest": workspace.digest,
                        "pending_call_ids": [call.call_id for call in pending],
                        "replay_candidate_call_ids": replay,
                        "reason": "controller_resumed_from_checkpoint",
                    },
                )
            ],
            workspace=workspace,
            pending=[call.call_id for call in pending],
        )
        return workspace

    # ---------------------------------------------------------------------- a turn

    async def _model_turn(self, workspace: _Workspace) -> str:
        self.gateway.check_compatibility(self.a.config, self.effective.definition())
        state = self.state
        index = len(state.turns)
        budget = self.effective.budget
        remaining_out = budget.output_tokens - state.budget.output_tokens
        max_output = min(self.a.config.max_output_tokens, remaining_out)
        completed = [
            CompletedTurn(
                t.index,
                Message(
                    role="assistant",
                    blocks=t.response.blocks,
                    provider_payload=t.response.provider_payload,
                    provider_payload_origin=t.response.provider_payload_origin,
                ),
                t.calls,
                tuple(t.results),
            )
            for t in state.turns
        ]
        try:
            context = build_context(
                task_message=self._task_message(),
                turns=completed,
                policy=self.effective.protocol.context,
                system=system_prompt(self.effective),
                tools=tool_specs(self.effective.tools),
                max_output_tokens=max_output,
                budget_text=self._budget_text(),
                request_template=self._request_template(),
            )
        except ContextBudgetExceeded:
            self._commit(
                [("budget_exhausted", {"dimension": "context_budget"})],
                workspace=workspace,
                pending=[],
            )
            state.exhausted = "context_budget"
            return "exhausted"
        if state.budget.input_tokens + context.estimated_tokens > budget.input_tokens:
            self._commit(
                [("budget_exhausted", {"dimension": "input_tokens"})],
                workspace=workspace,
                pending=[],
            )
            state.exhausted = "input_tokens"
            return "exhausted"
        request = ModelRequest(
            system=system_prompt(self.effective),
            messages=context.messages,
            tools=tool_specs(self.effective.tools),
            max_output_tokens=max_output,
            **self._request_template(),
        )
        try:
            result, elapsed = await self._call_model(request, index)
        except BudgetExhausted as error:
            # The gateway refused the call before sending it: the spending limit belongs to the
            # operator, not to the model's protocol budget, so the attempt stays resumable.
            raise SolveInterrupted(f"gateway spending limit reached: {error}") from error
        payload = self._turn_payload(
            index, result, elapsed, max_output, context.estimated_tokens, context.record()
        )
        events: list[tuple[str, dict[str, Any]]] = []
        if context.over_ceiling_events:
            events.append(
                (
                    "compaction",
                    {
                        "turn_index": index,
                        "events": [e.as_dict() for e in context.over_ceiling_events],
                    },
                )
            )
        events.append(("model_turn", payload))
        turn = _Turn(index, result.response)
        self._commit(events, workspace=workspace, pending=[c.call_id for c in turn.calls])
        state.turns.append(turn)
        return "ok"

    async def _run_pending(self, turn: _Turn, workspace: _Workspace) -> _Workspace:
        """Execute the turn's uncommitted tool calls one after another, in provider order."""
        budget_limits = self.effective.budget
        for call in list(turn.pending):
            state = self.state
            replayed = call.call_id in turn.started
            if call.name in MUTATING_MARKED and not replayed:
                self._commit(
                    [
                        (
                            "tool_started",
                            {
                                "turn_index": turn.index,
                                "tool_call_id": call.call_id,
                                "name": call.name,
                            },
                        )
                    ],
                    workspace=workspace,
                    pending=[c.call_id for c in turn.pending],
                )
                turn.started.add(call.call_id)
            over_calls = state.budget.tool_calls >= budget_limits.tool_calls
            over_time = state.budget.active_ms >= budget_limits.active_solve_seconds * 1000
            removed: list[str] = []
            violations: list[str] = []
            if over_calls or over_time:
                state.tool_budget_hit = True
                dimension = "tool_calls" if over_calls else "active_solve_seconds"
                from polycodebench_orchestration.solve.tools import _error

                result = _error(
                    call, ToolErrorCode.BUDGET_EXHAUSTED, f"{dimension} budget is exhausted"
                )
                mutated = False
            else:
                seconds_left = max(
                    1, budget_limits.active_solve_seconds - state.budget.active_ms // 1000
                )
                outcome = await self.runner.run(call, max_command_seconds=seconds_left)
                result, mutated = outcome.result, outcome.mutated
            if mutated:
                try:
                    workspace, removed, violations = await self._snapshot_after(workspace)
                except WorkspaceLimitExceeded as error:
                    raise _ModelFailure(f"workspace_limit_exceeded:{error}") from error
                notes = []
                if removed:
                    notes.append("removed links/special files: " + ", ".join(removed[:20]))
                if violations:
                    notes.append(
                        "WARNING protected files were modified: " + ", ".join(violations[:20])
                    )
                if notes:
                    result = result.model_copy(
                        update={"content": result.content + "\n[harness] " + "; ".join(notes)}
                    )
            seq = state.last_seq + 1
            after = apply_event_budget(
                state.budget,
                "tool_result",
                {
                    "result": {**result.model_dump(), "elapsed_ms": result.elapsed_ms},
                    "replayed": replayed,
                },
            )
            result = result.model_copy(
                update={
                    "event_seq": seq,
                    "replayed": replayed,
                    "budget_remaining": after.remaining(budget_limits),
                }
            )
            done_ids = {r.tool_call_id for r in turn.results} | {call.call_id}
            still_pending = [c.call_id for c in turn.calls if c.call_id not in done_ids]
            self._commit(
                [
                    (
                        "tool_result",
                        {
                            "turn_index": turn.index,
                            "tool_call_id": call.call_id,
                            "name": call.name,
                            "result": result.model_dump(mode="json"),
                            "replayed": replayed,
                            "removed_unsafe": removed,
                            "protected_violations": violations,
                            "workspace_digest": workspace.digest,
                        },
                    )
                ],
                workspace=workspace,
                pending=still_pending,
            )
            turn.results.append(result)
        return workspace

    async def _snapshot_after(
        self, previous: _Workspace
    ) -> tuple[_Workspace, list[str], list[str]]:
        await self.toolbox.sweep()  # no command descendant may outlive its call
        manifest, removed = await self.toolbox.snapshot()
        files, modes = extract_workspace(manifest)
        violations = protected_changes(files, self.a.protected_baseline())
        if _sha(manifest.archive_bytes) == previous.digest:
            return previous, removed, violations
        return self._new_workspace(manifest.archive_bytes, modes), removed, violations

    # --------------------------------------------------------------------- freezing

    async def _freeze_workspace(
        self, reason: str, workspace: _Workspace, *, dimension: str | None = None
    ) -> SolveResult:
        kind = self.a.contract.submission_kind
        turn = self.state.turns[-1] if self.state.turns else None
        if dimension is not None and not self.effective.protocol.freeze_on_budget_exhaustion:
            return self._record_failure(
                f"budget_exhausted:{dimension}:protocol_forbids_freeze",
                workspace,
                dimension=dimension,
            )
        if kind in {"files", "patch"}:
            await self.toolbox.sweep()
            try:
                manifest, _ = await self.toolbox.snapshot()
            except WorkspaceLimitExceeded as error:
                return self._record_failure(
                    f"workspace_limit_exceeded:{error}", workspace, dimension=dimension
                )
            files, modes = extract_workspace(manifest)
            final_workspace = (
                workspace
                if _sha(manifest.archive_bytes) == workspace.digest
                else self._new_workspace(manifest.archive_bytes, modes)
            )
            baseline = self.a.protected_baseline()
            if kind == "files":
                extraction = freeze_workspace_files(
                    files,
                    contract=self.a.contract,
                    required_outputs=self.a.required_outputs,
                    protected_baseline=baseline,
                )
            else:
                extraction = workspace_patch(
                    self.a.visible_files,
                    files,
                    contract=self.a.contract,
                    protected_baseline=baseline,
                )
            return self._freeze_extraction(
                extraction,
                workspace=final_workspace,
                reason_prefix=reason,
                budget_dimension=dimension,
            )
        if dimension is not None or turn is None:
            return self._record_failure(
                f"budget_exhausted:{dimension}:no_final_answer", workspace, dimension=dimension
            )
        extraction = extract_from_response(
            turn.response.text,
            contract=self.a.contract,
            rule="json_envelope",
            required_outputs=self.a.required_outputs,
        )
        return self._freeze_extraction(extraction, workspace=workspace, reason_prefix=reason)


__all__ = ["AgentSession", "LeaseLost", "SingleShotSession", "SolveResult", "apply_event_budget"]
