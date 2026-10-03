"""Self-repair session: one explicit versioned protocol, ordered rounds, public feedback only.

The controller runs outside the guest exactly like the solve sessions. Each repair round is one
single-shot model call whose prompt is rendered from the frozen task statement and the previous
round's PUBLIC case results; the previous round's hidden evaluation is not an input to anything
here and is never consulted to decide whether another round happens. Rounds stop when the frozen
``RepairLimits`` say so, and the final artifact is ``select_final``'s protocol choice, never the
best hidden-scoring round (Technical Spec 4.3, 17.2).

Infrastructure recovery maps to Technical Spec 7.5: a restart re-reads the durable run at its
round frontier and reissues the in-flight round's *logical* call (``repair-round-N``). The model
ledger's stored-response rule consumes a persisted response exactly once, and the run's own
recovery transition (``redeliver``) only ever records repeated delivery cost against the frontier
round - never a new round and never erased spend.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from polycodebench_core.canonical import sha256_bytes
from polycodebench_core.model_contracts import (
    CallScope,
    Message,
    ModelRequest,
    ProviderCallFailed,
    TextBlock,
    stable_json_bytes,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import TaskOutputContract
from polycodebench_core.repair_contracts import (
    PublicCaseResult,
    RepairBudgetExhausted,
    RepairFeedback,
    RepairProtocol,
    RepairRun,
    RoundTicket,
    begin_round,
    complete_round,
    feedback_from_public,
    freeze_selection,
    metrics_of,
    record_model_failure,
    start_repair_run,
)
from polycodebench_core.repair_prompts import round_task_message, system_prompt
from polycodebench_core.solve_contracts import SolveError, SolveInterrupted
from polycodebench_core.solve_extraction import extract_from_response
from polycodebench_persistence.repair_state import (
    NewDelivery,
    PostgresRepairRepository,
)

from polycodebench_orchestration.gateway.service import GatewayResult, ModelGateway

# A repair round that froze no candidate ends the loop: later rounds exist to repair candidates,
# and the protocol's selection then uses the frozen rounds it has.
MAX_REDELIVERIES_PER_ROUND = 2


@dataclass(frozen=True)
class RepairAssignment:
    """Everything a repair run may see. Hidden case runners are deliberately absent."""

    attempt_id: str
    repair_run_id: str
    scope: CallScope
    config: ModelConfig
    config_document_id: UUID
    sample_seed: int
    contract: TaskOutputContract
    required_outputs: tuple[str, ...]
    task_statement: str
    forbidden_markers: tuple[str, ...]
    public_case_ids: tuple[str, ...]
    #: Runs the PUBLIC case inventory on one candidate workspace. Hidden cases are evaluated
    #: after selection, outside this session, and can never influence a round.
    evaluate_public: Callable[[Mapping[str, bytes]], Sequence[PublicCaseResult]]


@dataclass(frozen=True)
class RoundArtifact:
    """One round's frozen candidate, for the evaluation stage to grade later."""

    round_index: int
    candidate_revision: int
    candidate_digest: str
    files: dict[str, bytes]


@dataclass(frozen=True)
class RepairOutcome:
    """Terminal state of one repair run. Selection is the protocol's; hidden scores are not here."""

    run: RepairRun
    artifacts: tuple[RoundArtifact, ...] = ()
    interrupted_round: int | None = None

    @property
    def selected_round_index(self) -> int | None:
        return self.run.selected_round_index

    @property
    def repair_round_count(self) -> int:
        return max(0, len(self.run.rounds) - 1)


class RepairSession:
    def __init__(
        self,
        *,
        repository: PostgresRepairRepository,
        gateway: ModelGateway,
        assignment: RepairAssignment,
        protocol: RepairProtocol,
        dispatch_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._a = assignment
        self._protocol = protocol
        self._dispatch_allowed = dispatch_allowed

    # ------------------------------------------------------------------------------- running

    async def run(self, *, resume: bool = False) -> RepairOutcome:
        if resume:
            run = self._repo.load_run(self._a.repair_run_id)
        else:
            run = start_repair_run(
                repair_run_id=self._a.repair_run_id,
                attempt_id=self._a.attempt_id,
                protocol=self._protocol,
                public_case_ids=self._a.public_case_ids,
            )
            self._repo.create_run(run)
        artifacts: list[RoundArtifact] = []
        while True:
            feedback = self._feedback_for(run)
            try:
                ticket = self._open_round(run, feedback)
            except RepairBudgetExhausted:
                break
            try:
                result, elapsed_ms, estimated_input = await self._call(ticket, run)
            except SolveInterrupted:
                return RepairOutcome(
                    run=run, artifacts=tuple(artifacts), interrupted_round=ticket.round_index
                )
            run, artifact = self._settle_round(
                run, ticket, result, elapsed_ms, estimated_input
            )
            if artifact is None:
                break
            artifacts.append(artifact)
        done = freeze_selection(run)
        self._repo.record_selection(done)
        return RepairOutcome(run=done, artifacts=tuple(artifacts))

    # ------------------------------------------------------------------------- one round

    def _feedback_for(self, run: RepairRun) -> RepairFeedback | None:
        index = run.current_round_index
        if index == 0:
            return None
        previous = run.rounds[-1]
        return feedback_from_public(
            index, previous.public_results, public_case_ids=set(run.public_case_ids)
        )

    def _open_round(self, run: RepairRun, feedback: RepairFeedback | None) -> RoundTicket:
        message = round_task_message(
            self._a.task_statement,
            round_index=run.current_round_index,
            limits=self._protocol.limits,
            feedback=feedback,
        )
        request = self._request(message)
        body = request.canonical_bytes()
        body_text = body.decode("utf-8", errors="replace")
        for marker in self._a.forbidden_markers:
            if marker and marker in body_text:
                raise SolveError("hidden task material would have reached the model")
        prompt_digest = sha256_bytes(
            stable_json_bytes({"system": system_prompt(), "message": message})
        )
        return begin_round(
            run,
            prompt_digest=prompt_digest,
            request_digest=sha256_bytes(body),
            feedback=feedback,
        )

    def _request(self, message: str) -> ModelRequest:
        config = self._a.config
        return ModelRequest(
            system=system_prompt(),
            messages=(Message(role="user", blocks=(TextBlock(text=message),)),),
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            seed=self._gateway.provider_seed(config, self._a.sample_seed),
            reasoning=config.reasoning,
        )

    async def _call(
        self, ticket: RoundTicket, run: RepairRun
    ) -> tuple[GatewayResult, int, int]:
        self._gateway.check_compatibility(
            self._a.config,
            self._protocol.to_definition(),
            requires_structured_output=False,
        )
        request = self._request(
            round_task_message(
                self._a.task_statement,
                round_index=ticket.round_index,
                limits=self._protocol.limits,
                feedback=None
                if ticket.feedback_digest is None
                else self._feedback_for(run),
            )
        )
        estimated_input = -(-len(request.canonical_bytes()) // 4)
        started = time.monotonic()
        try:
            result = await self._gateway.call(
                scope=self._a.scope,
                logical_call_key=ticket.logical_call_key,
                config=self._a.config,
                config_document_id=self._a.config_document_id,
                protocol=self._protocol.to_definition(),
                request=request,
                dispatch_allowed=self._dispatch_allowed,
            )
        except ProviderCallFailed as error:
            raise SolveInterrupted(
                f"provider call failed ({error.failure.kind.value}:{error.failure.code})"
            ) from error
        return result, int((time.monotonic() - started) * 1000), estimated_input

    def _settle_round(
        self,
        run: RepairRun,
        ticket: RoundTicket,
        result: GatewayResult,
        elapsed_ms: int,
        estimated_input: int,
    ) -> tuple[RepairRun, RoundArtifact | None]:
        usage = result.response.usage
        input_tokens = (
            usage.input_tokens if usage.input_tokens is not None else estimated_input
        )
        output_tokens = (
            usage.output_tokens
            if usage.output_tokens is not None
            else self._a.config.max_output_tokens
        )
        cost_micros = usage.reported_cost_micro_usd or 0
        extraction = extract_from_response(
            result.response.text,
            contract=self._a.contract,
            rule=self._protocol.base.single_shot_extraction or "json_envelope",
            required_outputs=list(self._a.required_outputs),
        )
        if extraction.validity != "valid":
            run = record_model_failure(
                run,
                ticket,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micros=cost_micros,
                active_ms=elapsed_ms,
            )
            self._commit_frontier(run, ticket, result, input_tokens, output_tokens,
                                  cost_micros, elapsed_ms)
            return run, None
        files = _files_of(extraction.payload)
        public_results = tuple(self._a.evaluate_public(files))
        run = complete_round(
            run,
            ticket,
            candidate_digest=extraction.digest(),
            public_results=public_results,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micros=cost_micros,
            active_ms=elapsed_ms,
        )
        self._commit_frontier(
            run, ticket, result, input_tokens, output_tokens, cost_micros, elapsed_ms
        )
        return run, RoundArtifact(
            round_index=ticket.round_index,
            candidate_revision=ticket.round_index + 1,
            candidate_digest=extraction.digest(),
            files=files,
        )

    def _commit_frontier(
        self,
        run: RepairRun,
        ticket: RoundTicket,
        result: GatewayResult,
        input_tokens: int,
        output_tokens: int,
        cost_micros: int,
        elapsed_ms: int,
    ) -> None:
        self._repo.commit_round(
            run,
            expected_rounds=ticket.round_index,
            delivery=NewDelivery(
                delivery_index=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micros=cost_micros,
                active_ms=elapsed_ms,
            ),
        )


def _files_of(payload: dict[str, Any]) -> dict[str, bytes]:
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise SolveError("a repair round submits files")
    files: dict[str, bytes] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or "path" not in entry
            or "content_b64" not in entry
        ):
            raise SolveError("malformed files envelope")
        files[str(entry["path"])] = base64.b64decode(str(entry["content_b64"]))
    return files


def inspect_repair_run(
    repository: PostgresRepairRepository, repair_run_id: str
) -> dict[str, Any]:
    """Read-only report of one repair run: rounds, feedback digests, selection, metrics."""
    run = repository.load_run(repair_run_id)
    metrics = (
        metrics_of(run) if run.state == "complete" else None
    )
    return {
        "repair_run_id": run.repair_run_id,
        "attempt_id": run.attempt_id,
        "protocol_id": run.protocol.protocol_id,
        "protocol_version": run.protocol.version,
        "protocol_digest": run.protocol.digest,
        "selection_rule": run.protocol.selection_rule,
        "state": run.state,
        "rounds": [
            {
                "round_index": entry.round_index,
                "candidate_revision": entry.candidate_revision,
                "candidate_digest": entry.candidate_digest,
                "prompt_digest": entry.prompt_digest,
                "request_digest": entry.request_digest,
                "feedback_digest": entry.feedback_digest,
                "deliveries": entry.deliveries,
                "state": entry.state,
                "public_results": [
                    result.model_dump(mode="json") for result in entry.public_results
                ],
            }
            for entry in run.rounds
        ],
        "spend": run.spend.model_dump(mode="json"),
        "selected_round_index": run.selected_round_index,
        "selection_uses_public_results_only": True,
        "metrics": metrics.model_dump(mode="json") if metrics else None,
    }
