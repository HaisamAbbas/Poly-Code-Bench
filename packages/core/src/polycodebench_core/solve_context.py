"""Bounded, deterministic context construction for the standard agent (Technical Spec 9.3).

The prompt is a pure function of committed state: the same transcript always yields byte-identical
requests, so a restarted controller asks the gateway for exactly the call it already recorded.
No wall-clock, random or environmental value enters it, and no model is used to summarise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from polycodebench_core.model_contracts import (
    Message,
    ModelRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
)
from polycodebench_core.solve_contracts import (
    BudgetRemaining,
    ContextBudgetExceeded,
    ContextPolicy,
    ToolResult,
)

SUMMARY_COMMAND_CHARS = 80


@dataclass(frozen=True)
class CompletedTurn:
    """One committed model turn: the assistant message, its tool calls and their results."""

    index: int
    assistant: Message
    calls: tuple[ToolCallBlock, ...]
    results: tuple[ToolResult, ...]


@dataclass(frozen=True)
class ContextEvent:
    kind: str  # "compaction" or "truncation"
    turn_index: int
    reason: str
    bytes_before: int
    bytes_after: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "turn_index": self.turn_index,
            "reason": self.reason,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
        }


@dataclass(frozen=True)
class ContextBuild:
    messages: tuple[Message, ...]
    estimated_tokens: int
    retained_full: tuple[int, ...]
    compacted_turns: tuple[int, ...]
    dropped_turns: tuple[int, ...]
    over_ceiling_events: tuple[ContextEvent, ...] = field(default_factory=tuple)

    def record(self) -> dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "retained_full": list(self.retained_full),
            "compacted_turns": list(self.compacted_turns),
            "dropped_turns": list(self.dropped_turns),
            "events": [event.as_dict() for event in self.over_ceiling_events],
        }


def estimate_tokens(data: bytes) -> int:
    """``utf8_bytes_div4_v1``: ceil(bytes / 4)."""
    return -(-len(data) // 4)


def summarize_result(call: ToolCallBlock | None, result: ToolResult) -> str:
    """Deterministic metadata summary of an old tool output (no model involved)."""
    detail: dict[str, Any] = {}
    if call is not None and call.arguments_valid:
        try:
            arguments = json.loads(call.arguments_json)
        except ValueError:
            arguments = {}
        for key in ("path", "pattern", "path_glob", "working_directory", "group_ids"):
            if key in arguments:
                detail[key] = arguments[key]
        if "command" in arguments:
            detail["command"] = str(arguments["command"])[:SUMMARY_COMMAND_CHARS]
    record: dict[str, Any] = {
        "compacted": True,
        "tool": result.name,
        "status": result.status,
        "event_seq": result.event_seq,
        "original_bytes": result.original_bytes,
        **detail,
    }
    if result.error_code:
        record["error_code"] = result.error_code
    if result.artifact_id:
        record["artifact_id"] = result.artifact_id
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def render_budget(remaining: BudgetRemaining, limits: dict[str, int]) -> str:
    return (
        "Budget remaining "
        + json.dumps(remaining.model_dump(), sort_keys=True, separators=(",", ":"))
        + " of "
        + json.dumps(limits, sort_keys=True, separators=(",", ":"))
        + "."
    )


CLIP_LIMITS = (2000, 500, 100)


def _clip(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return (
        data[:limit].decode("utf-8", errors="ignore")
        + "\n[harness] clipped to fit the context window"
    )


def _turn_messages(
    turn: CompletedTurn, *, compact: bool, extra_text: str | None = None, clip: int | None = None
) -> list[Message]:
    messages = [turn.assistant]
    if turn.calls:
        by_id = {call.call_id: call for call in turn.calls}
        blocks: list[ToolResultBlock | TextBlock] = []
        for result in turn.results:
            call = by_id.get(result.tool_call_id)
            content = summarize_result(call, result) if compact else result.model_text()
            if clip is not None and not compact:
                content = _clip(content, clip)
            blocks.append(
                ToolResultBlock(
                    call_id=result.tool_call_id,
                    name=result.name,
                    content=content,
                    is_error=result.status == "error",
                )
            )
        if extra_text is not None:
            blocks.append(TextBlock(text=extra_text))
        if blocks:
            messages.append(Message(role="user", blocks=tuple(blocks)))
    return messages


def build_context(
    *,
    task_message: str,
    turns: list[CompletedTurn],
    policy: ContextPolicy,
    system: str,
    tools: tuple[ToolSpec, ...],
    max_output_tokens: int,
    budget_text: str,
    request_template: dict[str, Any],
) -> ContextBuild:
    """Latest ``retain_recent_turns`` turns in full; older tool output as metadata summaries.

    If the estimate still exceeds the ceiling, drop the oldest summarised turns whole (so
    tool-call/result pairs stay valid), then summarise recent turns oldest-first. The latest turn
    and the task instructions are core context: if they do not fit, ``ContextBudgetExceeded``.
    """
    events: list[ContextEvent] = []
    dropped: list[int] = []
    compacted: set[int] = {
        turn.index for turn in turns[: max(0, len(turns) - policy.retain_recent_turns)]
    }
    active = list(turns)
    clips: dict[int, int] = {}

    def assemble() -> tuple[tuple[Message, ...], int]:
        messages: list[Message] = []
        last_index = active[-1].index if active else None
        head_text = task_message + ("" if active else "\n\n" + budget_text)
        messages.append(Message(role="user", blocks=(TextBlock(text=head_text),)))
        for turn in active:
            extra = budget_text if turn.index == last_index else None
            messages.extend(
                _turn_messages(
                    turn,
                    compact=turn.index in compacted,
                    extra_text=extra,
                    clip=clips.get(turn.index),
                )
            )
        request = ModelRequest(
            system=system,
            messages=tuple(messages),
            tools=tools,
            max_output_tokens=max_output_tokens,
            **request_template,
        )
        return tuple(messages), estimate_tokens(request.canonical_bytes())

    messages, estimate = assemble()
    ceiling = policy.max_input_context_tokens
    while estimate > ceiling:
        before = estimate * 4
        older = [turn for turn in active if turn.index in compacted]
        if older:
            victim = older[0]
            active.remove(victim)
            dropped.append(victim.index)
            reason = "over_ceiling_drop_oldest_summarised_turn"
            kind = "truncation"
        else:
            candidates = [turn for turn in active[:-1] if turn.index not in compacted]
            if candidates:
                victim = candidates[0]
                compacted.add(victim.index)
                reason = "over_ceiling_summarise_recent_turn"
            else:
                # Only the latest turn is left: clip its tool output progressively (recorded).
                victim = active[-1]
                current = clips.get(victim.index)
                smaller = [limit for limit in CLIP_LIMITS if current is None or limit < current]
                if not victim.results or not smaller:
                    raise ContextBudgetExceeded(
                        f"required context needs about {estimate} tokens; ceiling is {ceiling}"
                    )
                clips[victim.index] = smaller[0]
                reason = f"over_ceiling_clip_latest_turn_results_to_{smaller[0]}_bytes"
            kind = "compaction"
        messages, estimate = assemble()
        events.append(ContextEvent(kind, victim.index, reason, before, estimate * 4))
    kept = {turn.index for turn in active}
    return ContextBuild(
        messages=messages,
        estimated_tokens=estimate,
        retained_full=tuple(t.index for t in active if t.index not in compacted),
        compacted_turns=tuple(sorted(compacted & kept)),
        dropped_turns=tuple(sorted(dropped)),
        over_ceiling_events=tuple(events),
    )
