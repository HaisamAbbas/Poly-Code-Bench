"""Host-side tool execution: validate, run in the guest, shape a bounded model-visible result.

Nothing model-authored reaches a host shell. Arguments are validated against strict schemas,
paths are normalised and checked against the protected list before anything is sent, and the
guest re-checks them. A tool error is a typed, model-visible result; only a failing guest is an
infrastructure problem (``GuestInfrastructureError`` propagates to the session).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from polycodebench_core.model_contracts import ToolCallBlock, stable_json_bytes
from polycodebench_core.solve_contracts import (
    ARGS_MODELS,
    LIST_MAX_ENTRIES,
    MODEL_VISIBLE_COMMAND_BYTES,
    SEARCH_MAX_BYTES,
    TOOL_NAMES,
    ApplyPatchArgs,
    ListFilesArgs,
    PathForbidden,
    ReadFileArgs,
    RunCommandArgs,
    RunPublicTestsArgs,
    SearchArgs,
    ToolErrorCode,
    ToolResult,
    is_protected,
    normalize_workspace_path,
)
from polycodebench_core.solve_extraction import patch_paths
from polycodebench_runner.guest_tools import (
    GuestInfrastructureError,
    GuestToolbox,
    GuestToolFailure,
)
from pydantic import ValidationError

from polycodebench_orchestration.gateway.service import ResponseStore
from polycodebench_orchestration.solve.types import SolveAssignment

LIST_PAGE_BYTES = 48 * 1024
ARCHIVED_OUTPUT_TOOLS = frozenset({"run_command", "run_public_tests"})


@dataclass(frozen=True)
class ToolOutcome:
    """A tool result plus what the session must do about the workspace afterwards."""

    result: ToolResult
    mutated: bool


def _error(call: ToolCallBlock, code: str, message: str, *, elapsed_ms: int = 0) -> ToolResult:
    text = f"{code}: {message}"[:2000]
    data = text.encode()
    return ToolResult(
        event_seq=0,
        tool_call_id=call.call_id,
        name=call.name,
        status="error",
        error_code=code,
        content=text,
        original_bytes=len(data),
        returned_bytes=len(data),
        elapsed_ms=elapsed_ms,
    )


def _clip(text: str, limit: int) -> tuple[str, bool]:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text, False
    head = data[: limit * 3 // 4].decode("utf-8", "ignore")
    tail = data[-(limit // 4) :].decode("utf-8", "ignore")
    omitted = len(data) - len(head.encode()) - len(tail.encode())
    return f"{head}\n...[{omitted} bytes omitted]...\n{tail}", True


def validation_summary(error: ValidationError) -> str:
    """A short, deterministic description of what was wrong, without echoing argument values."""
    parts = []
    for item in error.errors(include_input=False)[:6]:
        where = ".".join(str(piece) for piece in item["loc"]) or "arguments"
        parts.append(f"{where}: {item['type']}")
    return "invalid arguments (" + "; ".join(parts) + ")"


class ToolRunner:
    def __init__(
        self,
        toolbox: GuestToolbox,
        assignment: SolveAssignment,
        store: ResponseStore,
        *,
        tools: list[str],
        per_command_seconds: int,
    ) -> None:
        self._toolbox = toolbox
        self._assignment = assignment
        self._store = store
        self._tools = set(tools)
        self._per_command_limit = per_command_seconds
        self._per_command = per_command_seconds

    async def run(self, call: ToolCallBlock, *, max_command_seconds: int) -> ToolOutcome:
        """Run one call; commands are capped by the task limit and the time still available."""
        self._per_command = max(1, min(self._per_command_limit, max_command_seconds))
        started = time.monotonic()
        name = call.name
        if name not in TOOL_NAMES:
            return ToolOutcome(
                _error(call, ToolErrorCode.UNKNOWN_TOOL, f"no tool named {name!r}"), False
            )
        if name not in self._tools:
            return ToolOutcome(
                _error(call, ToolErrorCode.TOOL_NOT_ALLOWED, f"{name} is not available here"), False
            )
        if not call.arguments_valid:
            return ToolOutcome(
                _error(call, ToolErrorCode.INVALID_ARGUMENTS, "arguments were not a JSON object"),
                False,
            )
        try:
            args = ARGS_MODELS[name].model_validate_json(call.arguments_json, strict=True)
        except ValidationError as error:
            return ToolOutcome(
                _error(call, ToolErrorCode.INVALID_ARGUMENTS, validation_summary(error)), False
            )
        try:
            outcome = await self._dispatch(call, args)
        except PathForbidden as error:
            outcome = ToolOutcome(_error(call, ToolErrorCode.PATH_FORBIDDEN, str(error)), False)
        except GuestToolFailure as failure:
            outcome = ToolOutcome(
                _error(call, failure.code or ToolErrorCode.RESTORED_TOOL_FAILURE, failure.message),
                False,  # a rejected call changed nothing (patches are all-or-nothing)
            )
        except GuestInfrastructureError:
            # A helper killed by the model's own command (memory, signals) is a tool failure.
            # If the sandbox cannot answer a probe either, it really is infrastructure.
            await self._toolbox.process_count()
            outcome = ToolOutcome(
                _error(call, ToolErrorCode.RESTORED_TOOL_FAILURE, "the tool helper terminated"),
                True,
            )
        elapsed = int((time.monotonic() - started) * 1000)
        return ToolOutcome(
            outcome.result.model_copy(update={"elapsed_ms": elapsed}), outcome.mutated
        )

    # ------------------------------------------------------------------------ dispatch

    async def _dispatch(self, call: ToolCallBlock, args: Any) -> ToolOutcome:
        if isinstance(args, ListFilesArgs):
            return await self._list(call, args)
        if isinstance(args, ReadFileArgs):
            return await self._read(call, args)
        if isinstance(args, SearchArgs):
            return await self._search(call, args)
        if isinstance(args, ApplyPatchArgs):
            return await self._patch(call, args)
        if isinstance(args, RunCommandArgs):
            return await self._command(call, args)
        assert isinstance(args, RunPublicTestsArgs)
        return await self._public_tests(call, args)

    def _ok(
        self,
        call: ToolCallBlock,
        content: str,
        *,
        original_bytes: int | None = None,
        truncated: bool = False,
        artifact_id: str | None = None,
        mutated: bool = False,
    ) -> ToolOutcome:
        returned = len(content.encode("utf-8"))
        return ToolOutcome(
            ToolResult(
                event_seq=0,
                tool_call_id=call.call_id,
                name=call.name,
                status="ok",
                content=content,
                truncated=truncated,
                original_bytes=returned if original_bytes is None else original_bytes,
                returned_bytes=returned,
                artifact_id=artifact_id,
            ),
            mutated,
        )

    async def _list(self, call: ToolCallBlock, args: ListFilesArgs) -> ToolOutcome:
        path = normalize_workspace_path(args.path, allow_root=True)
        reply = await self._toolbox.list_files(path, args.depth, args.cursor)
        entries = reply["entries"][:LIST_MAX_ENTRIES]
        lines: list[str] = []
        used = 0
        cut = False
        for entry in entries:
            line = f"{entry['type']}\t{entry['size']}\t{entry['path']}"
            if used + len(line) + 1 > LIST_PAGE_BYTES:
                cut = True
                break
            lines.append(line)
            used += len(line) + 1
        next_cursor = reply["next_cursor"]
        if cut:
            next_cursor = entries[len(lines) - 1]["path"] if lines else args.cursor
        footer = f"next_cursor: {next_cursor}" if next_cursor else "end of listing"
        full = len(entries) + int(reply.get("total_remaining", 0))
        return self._ok(
            call,
            "\n".join([*lines, footer]),
            original_bytes=full,
            truncated=bool(next_cursor),
        )

    async def _read(self, call: ToolCallBlock, args: ReadFileArgs) -> ToolOutcome:
        path = normalize_workspace_path(args.path)
        reply = await self._toolbox.read_file(path, args.start_line, args.max_lines)
        if reply.get("too_large"):
            return self._ok(
                call, f"{path}: file is larger than the readable limit ({reply['size']} bytes)"
            )
        if reply["binary"]:
            return self._ok(
                call,
                f"{path}: binary file, {reply['size']} bytes, {reply['sha256']}",
                original_bytes=int(reply["size"]),
            )
        footer = (
            f"[{path} lines {reply['start_line']}-{reply['end_line']} of {reply['total_lines']}; "
            f"{reply['sha256']}"
            + (f"; continue at line {reply['next_start_line']}" if reply["next_start_line"] else "")
            + "]"
        )
        content = f"{reply['text']}\n{footer}" if reply["text"] else footer
        return self._ok(
            call, content, original_bytes=int(reply["size"]), truncated=bool(reply["truncated"])
        )

    async def _search(self, call: ToolCallBlock, args: SearchArgs) -> ToolOutcome:
        reply = await self._toolbox.search(args.pattern, args.regex, args.path_glob, args.cursor)
        lines = [f"{m['path']}:{m['line']}: {m['text']}" for m in reply["matches"]]
        footer = (
            f"next_cursor: {reply['next_cursor']}" if reply["next_cursor"] else "end of matches"
        )
        content = "\n".join([*lines, footer])
        content, clipped = _clip(content, SEARCH_MAX_BYTES)
        return self._ok(call, content, truncated=bool(reply["next_cursor"]) or clipped)

    async def _patch(self, call: ToolCallBlock, args: ApplyPatchArgs) -> ToolOutcome:
        paths = patch_paths(args.diff)
        if paths is None:
            raise PathForbidden("patch names a path outside the workspace")
        for path in paths:
            if is_protected(path, self._assignment.protected_paths):
                return ToolOutcome(
                    _error(
                        call, ToolErrorCode.PROTECTED_PATH, f"{path} is protected and cannot change"
                    ),
                    False,
                )
        reply = await self._toolbox.apply_patch(
            args.diff,
            self._assignment.protected_paths,
            self._assignment.contract.maximum_file_bytes,
        )
        lines = [
            f"{f['action']} {f['path']} (+{f['added']} -{f['removed']}, {f['hunks']} hunks)"
            for f in reply["files"]
        ]
        return self._ok(call, "patch applied\n" + "\n".join(lines), mutated=True)

    async def _command(self, call: ToolCallBlock, args: RunCommandArgs) -> ToolOutcome:
        cwd = normalize_workspace_path(args.working_directory, allow_root=True)
        seconds = min(args.timeout_seconds, self._per_command)
        reply = await self._toolbox.run_command(args.command, cwd, seconds)
        return await self._command_result(call, reply, label=None)

    async def _public_tests(self, call: ToolCallBlock, args: RunPublicTestsArgs) -> ToolOutcome:
        groups = self._assignment.public_groups
        if not groups:
            return ToolOutcome(
                _error(call, ToolErrorCode.NO_PUBLIC_TESTS, "this task publishes no public tests"),
                False,
            )
        unknown = [gid for gid in args.group_ids if gid not in groups]
        if unknown:
            return ToolOutcome(
                _error(
                    call,
                    ToolErrorCode.UNKNOWN_TEST_GROUP,
                    "unknown group(s): "
                    + ", ".join(unknown)
                    + "; available: "
                    + ", ".join(sorted(groups)),
                ),
                False,
            )
        sections: list[str] = []
        replies: list[dict[str, Any]] = []
        for gid in dict.fromkeys(args.group_ids):
            group = groups[gid]
            cwd = normalize_workspace_path(group.cwd, allow_root=True)
            reply = await self._toolbox.run_argv(
                group.argv, cwd, min(group.timeout_seconds, self._per_command)
            )
            replies.append({"group": gid, **reply})
            outcome = await self._command_result(call, reply, label=gid, archive=False)
            sections.append(outcome.result.content)
        content, clipped = _clip("\n\n".join(sections), MODEL_VISIBLE_COMMAND_BYTES)
        artifact = self._archive(replies)
        total = sum(int(r["stdout_bytes"]) + int(r["stderr_bytes"]) for r in replies)
        return self._ok(
            call,
            content,
            original_bytes=total,
            truncated=clipped or any(r["stdout_bytes"] > 16_384 for r in replies),
            artifact_id=artifact,
            mutated=True,
        )

    async def _command_result(
        self,
        call: ToolCallBlock,
        reply: dict[str, Any],
        *,
        label: str | None,
        archive: bool = True,
    ) -> ToolOutcome:
        half = MODEL_VISIBLE_COMMAND_BYTES // 2
        stdout, cut_out = _clip(reply["stdout"], half)
        stderr, cut_err = _clip(reply["stderr"], half)
        status = (
            "timed_out"
            if reply["timed_out"]
            else f"signal {reply['signal']}"
            if reply["signal"] is not None
            else f"exit {reply['exit_code']}"
        )
        header = (
            (f"[group {label}] " if label else "")
            + f"{status}; elapsed {reply['elapsed_ms']} ms; "
            + f"cpu {reply['user_ms'] + reply['system_ms']} ms; max_rss {reply['max_rss_kb']} KiB"
        )
        content = f"{header}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        artifact = self._archive(reply) if archive else None
        outcome = self._ok(
            call,
            content,
            original_bytes=int(reply["stdout_bytes"]) + int(reply["stderr_bytes"]),
            truncated=cut_out
            or cut_err
            or reply["stdout_bytes"] > 16_384
            or reply["stderr_bytes"] > 16_384,
            artifact_id=artifact,
            mutated=True,
        )
        if reply["timed_out"]:
            return ToolOutcome(
                outcome.result.model_copy(
                    update={"status": "error", "error_code": ToolErrorCode.TIMEOUT}
                ),
                True,
            )
        return outcome

    def _archive(self, payload: Any) -> str:
        return str(
            self._store.put(
                stable_json_bytes(payload), kind="tool_output", media_type="application/json"
            )
        )


def arguments_preview(call: ToolCallBlock, limit: int = 300) -> str:
    try:
        return json.dumps(json.loads(call.arguments_json), sort_keys=True)[:limit]
    except ValueError:
        return call.arguments_json[:limit]
