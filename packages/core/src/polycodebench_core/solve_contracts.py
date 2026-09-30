"""Solve-session contracts: protocol identity, tool schemas, results, budgets and outcomes.

Pure data and rules only. The controllers (orchestration), the guest tool helper (runner) and
the checkpoint repository (persistence) all build on these types so that what a model is told,
what a tool may do and what is recorded agree by construction.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from polycodebench_core.application_errors import ServiceError
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.model_contracts import Strict, ToolSpec
from polycodebench_core.models import (
    SAFE_JSON_INTEGER_MAX,
    ContractModel,
    ProtocolConstraints,
    ProtocolDefinition,
    Slug,
)

TOOL_NAMES = (
    "list_files",
    "read_file",
    "search",
    "apply_patch",
    "run_command",
    "run_public_tests",
)
MUTATING_TOOLS = frozenset({"apply_patch", "run_command", "run_public_tests"})
RESERVED_PREFIX = ".pcb_"

# Tool limits from Technical Spec 9.2.
LIST_MAX_ENTRIES = 1000
LIST_MAX_DEPTH = 10
READ_MAX_LINES = 400
READ_MAX_BYTES = 64 * 1024
SEARCH_MAX_MATCHES = 200
SEARCH_MAX_BYTES = 64 * 1024
PATCH_MAX_BYTES = 1024 * 1024
COMMAND_MAX_CHARS = 8192
MODEL_VISIBLE_COMMAND_BYTES = 32 * 1024
MAX_PATH_CHARS = 512
MAX_PATH_DEPTH = 40

Tokens = Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]


class SolveError(ServiceError):
    code = "SOLVE_ERROR"
    status_code = 500


class SolveInterrupted(SolveError):
    """Infrastructure interruption (guest died, lease lost). The work is resumable; this is
    never a model failure and never changes what the model was told."""

    code = "SOLVE_INTERRUPTED"
    status_code = 503


class CheckpointMismatch(SolveError):
    """A checkpoint's workspace and transcript do not belong together, or belong to another
    protocol. Restoring it would pair different states, so restoration is refused."""

    code = "CHECKPOINT_MISMATCH"
    status_code = 409


class ToolErrorCode:
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    PATH_FORBIDDEN = "path_forbidden"
    PROTECTED_PATH = "protected_path"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PATCH_REJECTED = "patch_rejected"
    TIMEOUT = "timeout"
    REGEX_INVALID = "regex_invalid"
    SEARCH_TIMEOUT = "search_timeout"
    NOT_FOUND = "not_found"
    NOT_A_FILE = "not_a_file"
    NOT_A_DIRECTORY = "not_a_directory"
    NO_PUBLIC_TESTS = "no_public_tests"
    UNKNOWN_TEST_GROUP = "unknown_test_group"
    RESTORED_TOOL_FAILURE = "tool_failure"


class PathForbidden(ValueError):
    """A workspace path that a tool must never resolve."""


def normalize_workspace_path(raw: object, *, allow_root: bool = False) -> str:
    """Canonical relative POSIX path, or ``PathForbidden``. No filesystem access."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PATH_CHARS:
        raise PathForbidden("path must be a non-empty relative string")
    if "\\" in raw or raw.startswith("/"):
        raise PathForbidden("path must be relative and use forward slashes")
    if any(unicodedata.category(ch)[0] == "C" for ch in raw):
        raise PathForbidden("path contains control characters")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PathForbidden("path cannot leave the workspace")
    if any(part.startswith(RESERVED_PREFIX) for part in parts):
        raise PathForbidden("path is reserved for the harness")
    if len(parts) > MAX_PATH_DEPTH:
        raise PathForbidden("path is too deep")
    if not parts:
        if allow_root:
            return "."
        raise PathForbidden("path must name a file")
    return "/".join(parts)


def is_protected(path: str, protected_paths: list[str] | tuple[str, ...]) -> bool:
    """True when ``path`` is, or lies beneath, a protected path."""
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in protected_paths)


# ---------------------------------------------------------------------------- protocols


class ContextPolicy(ContractModel):
    kind: Literal["context_policy"]
    retain_recent_turns: Annotated[int, Field(strict=True, ge=1, le=64)]
    max_input_context_tokens: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    # Estimated tokens = ceil(utf-8 bytes / 4). Provider-reported input tokens are recorded next
    # to the estimate every turn so a discrepancy is visible (spec 9.3).
    token_counter: Literal["utf8_bytes_div4_v1"]
    summarizer: Literal["none"]


class SolveBudget(ContractModel):
    kind: Literal["solve_budget"]
    model_turns: Tokens
    tool_calls: Tokens
    active_solve_seconds: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    per_command_seconds: Annotated[int, Field(strict=True, gt=0, le=3600)] | None
    input_tokens: Tokens
    output_tokens: Tokens


class SolveProtocol(ContractModel):
    """A frozen solve protocol. Its digest is part of every run, event and checkpoint."""

    kind: Literal["solve_protocol"]
    protocol_id: Slug
    version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    mode: Literal["single_shot", "standard_agent"]
    tools: list[Slug]
    public_test_feedback: bool
    single_shot_extraction: Literal["json_envelope", "single_fenced_block"] | None
    context: ContextPolicy
    budget: SolveBudget
    freeze_on_budget_exhaustion: bool
    prompt_policy: Slug

    @model_validator(mode="after")
    def coherent(self) -> SolveProtocol:
        if len(self.tools) != len(set(self.tools)) or not set(self.tools) <= set(TOOL_NAMES):
            raise ValueError("tools must be unique members of the standard tool set")
        if self.mode == "single_shot":
            if (
                self.tools
                or self.budget.model_turns != 1
                or self.budget.tool_calls != 0
                or self.single_shot_extraction is None
                or self.public_test_feedback
            ):
                raise ValueError(
                    "single-shot protocols have one turn, no tools and an extraction rule"
                )
        else:
            if not self.tools or self.single_shot_extraction is not None:
                raise ValueError("agent protocols declare tools and no single-shot extraction")
            if self.budget.per_command_seconds is None:
                raise ValueError("agent protocols need a per-command time limit")
            if self.public_test_feedback and "run_public_tests" not in self.tools:
                raise ValueError("public test feedback requires the run_public_tests tool")
        return self

    def to_definition(self) -> ProtocolDefinition:
        """The shape the model gateway validates capabilities against."""
        return ProtocolDefinition(
            schema_version=1,
            kind="protocol",
            protocol_id=self.protocol_id,
            version=self.version,
            mode=self.mode,
            allowed_tools=list(self.tools),
            public_test_feedback=self.public_test_feedback,
            hidden_feedback=False,
            network_policy="disabled",
            maximum_turns=self.budget.model_turns,
            maximum_tool_calls=self.budget.tool_calls,
            maximum_wall_seconds=self.budget.active_solve_seconds,
            maximum_input_context_tokens=self.context.max_input_context_tokens,
        )


class EffectiveProtocol(ContractModel):
    """The protocol after intersecting with one task's constraints (minimum of ceilings)."""

    kind: Literal["effective_protocol"]
    protocol_digest: str
    protocol: SolveProtocol
    tools: list[Slug]
    budget: SolveBudget

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset()

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)

    def definition(self) -> ProtocolDefinition:
        """The gateway-facing protocol with this task's effective tools and ceilings."""
        base = self.protocol.to_definition()
        return base.model_copy(
            update={
                "allowed_tools": list(self.tools),
                "maximum_turns": self.budget.model_turns,
                "maximum_tool_calls": self.budget.tool_calls,
                "maximum_wall_seconds": self.budget.active_solve_seconds,
            }
        )


def resolve_effective_protocol(
    protocol: SolveProtocol, constraints: ProtocolConstraints
) -> EffectiveProtocol:
    """Apply a task's declared ceilings. A task may narrow tools and budgets, never widen them,
    and a task that allows hidden feedback is not solvable by these protocols."""
    if constraints.hidden_feedback:
        raise SolveError("task requires hidden feedback, which solve sessions never receive")
    if constraints.protocol_id != protocol.protocol_id:
        raise SolveError("task constraints were frozen for a different protocol")
    tools = [name for name in protocol.tools if name in set(constraints.allowed_tools)]
    if not constraints.public_test_feedback:
        tools = [name for name in tools if name != "run_public_tests"]
    if protocol.mode == "standard_agent" and not tools:
        raise SolveError("task constraints leave the agent protocol with no tools")
    if protocol.mode == "single_shot":
        tools = []
    base = protocol.budget
    budget = SolveBudget(
        schema_version=1,
        kind="solve_budget",
        model_turns=min(base.model_turns, constraints.maximum_model_turns),
        tool_calls=min(base.tool_calls, constraints.maximum_tool_calls),
        active_solve_seconds=min(base.active_solve_seconds, constraints.maximum_wall_seconds),
        per_command_seconds=base.per_command_seconds,
        input_tokens=base.input_tokens,
        output_tokens=base.output_tokens,
    )
    if budget.model_turns < 1:
        raise SolveError("task constraints leave no model turn")
    return EffectiveProtocol(
        schema_version=1,
        kind="effective_protocol",
        protocol_digest=canonical_document_digest(protocol),
        protocol=protocol,
        tools=tools,
        budget=budget,
    )


# ------------------------------------------------------------------------- tool arguments


class ToolArgs(Strict):
    """Strict argument base: unknown fields and type coercions are rejected."""


class ListFilesArgs(ToolArgs):
    path: str = "."
    depth: Annotated[int, Field(ge=1, le=LIST_MAX_DEPTH)] = 1
    cursor: Annotated[str, Field(max_length=MAX_PATH_CHARS)] | None = None


class ReadFileArgs(ToolArgs):
    path: str
    start_line: Annotated[int, Field(ge=1, le=10_000_000)] = 1
    max_lines: Annotated[int, Field(ge=1, le=READ_MAX_LINES)] = READ_MAX_LINES


class SearchArgs(ToolArgs):
    pattern: Annotated[str, Field(min_length=1, max_length=512)]
    regex: bool = False
    path_glob: Annotated[str, Field(min_length=1, max_length=256)] = "*"
    cursor: Annotated[str, Field(max_length=MAX_PATH_CHARS + 16)] | None = None


class ApplyPatchArgs(ToolArgs):
    diff: Annotated[str, Field(min_length=1, max_length=PATCH_MAX_BYTES)]


class RunCommandArgs(ToolArgs):
    command: Annotated[str, Field(min_length=1, max_length=COMMAND_MAX_CHARS)]
    working_directory: str = "."
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 30

    @field_validator("command")
    @classmethod
    def no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("command cannot contain NUL")
        return value


class RunPublicTestsArgs(ToolArgs):
    group_ids: Annotated[list[Slug], Field(min_length=1, max_length=32)]


ARGS_MODELS: dict[str, type[ToolArgs]] = {
    "list_files": ListFilesArgs,
    "read_file": ReadFileArgs,
    "search": SearchArgs,
    "apply_patch": ApplyPatchArgs,
    "run_command": RunCommandArgs,
    "run_public_tests": RunPublicTestsArgs,
}

TOOL_DESCRIPTIONS = {
    "list_files": (
        "List files and directories beneath a workspace directory, sorted, at most "
        f"{LIST_MAX_ENTRIES} entries per page. Pass the returned next_cursor to continue."
    ),
    "read_file": (
        f"Read a text file with line numbers: at most {READ_MAX_LINES} lines and "
        f"{READ_MAX_BYTES // 1024} KiB per call. Binary files return metadata only."
    ),
    "search": (
        "Search workspace text files for a literal or regular-expression pattern; at most "
        f"{SEARCH_MAX_MATCHES} matches per page. Pass next_cursor to continue."
    ),
    "apply_patch": (
        "Apply a unified diff atomically: either every hunk applies or nothing changes. "
        "Paths are relative to the workspace root; protected files cannot be modified."
    ),
    "run_command": (
        "Run shell text inside the isolated sandbox with no network. All processes started by "
        "the command are stopped when it finishes; files it creates persist."
    ),
    "run_public_tests": (
        "Run the named public test groups published with the task. Only public results are "
        "returned."
    ),
}


def tool_specs(names: list[str] | tuple[str, ...]) -> tuple[ToolSpec, ...]:
    """Gateway tool specs, in the canonical standard order, for the effective tool set."""
    specs = []
    for name in TOOL_NAMES:
        if name not in names:
            continue
        schema = ARGS_MODELS[name].model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        specs.append(
            ToolSpec(name=name, description=TOOL_DESCRIPTIONS[name], parameters_schema=schema)
        )
    return tuple(specs)


# ------------------------------------------------------------------------------ results


class BudgetRemaining(Strict):
    model_turns: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    active_seconds: int


class ToolResult(Strict):
    """What a tool call produced. ``content`` is the only field shown to the model."""

    event_seq: int
    tool_call_id: str
    name: str
    status: Literal["ok", "error"]
    error_code: str | None = None
    content: str
    truncated: bool = False
    original_bytes: int = 0
    returned_bytes: int = 0
    artifact_id: str | None = None
    budget_remaining: BudgetRemaining | None = None
    elapsed_ms: int = 0
    replayed: bool = False

    def model_text(self) -> str:
        """Deterministic rendering placed in the prompt."""
        header = {
            "event_seq": self.event_seq,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "truncated": self.truncated,
            "original_bytes": self.original_bytes,
            "returned_bytes": self.returned_bytes,
        }
        if self.error_code:
            header["error_code"] = self.error_code
        if self.artifact_id:
            header["artifact_id"] = self.artifact_id
        if self.budget_remaining is not None:
            header["budget_remaining"] = self.budget_remaining.model_dump()
        import json

        return json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n" + self.content


class BudgetState(Strict):
    """Cumulative, committed consumption. Derived only from committed events."""

    turns: int = 0
    tool_calls: int = 0
    invalid_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    active_ms: int = 0
    repeated_compute_ms: int = 0

    def remaining(self, budget: SolveBudget) -> BudgetRemaining:
        return BudgetRemaining(
            model_turns=max(0, budget.model_turns - self.turns),
            tool_calls=max(0, budget.tool_calls - self.tool_calls),
            input_tokens=max(0, budget.input_tokens - self.input_tokens),
            output_tokens=max(0, budget.output_tokens - self.output_tokens),
            active_seconds=max(0, budget.active_solve_seconds - self.active_ms // 1000),
        )

    def exhausted(self, budget: SolveBudget) -> str | None:
        """The first exhausted dimension before another model turn, or ``None``."""
        if self.turns >= budget.model_turns:
            return "model_turns"
        if self.output_tokens >= budget.output_tokens:
            return "output_tokens"
        if self.input_tokens >= budget.input_tokens:
            return "input_tokens"
        if self.active_ms >= budget.active_solve_seconds * 1000:
            return "active_solve_seconds"
        return None

    def tool_budget_exhausted(self, budget: SolveBudget) -> bool:
        return self.tool_calls >= budget.tool_calls

    def plus(self, **delta: int) -> BudgetState:
        data = self.model_dump()
        for key, value in delta.items():
            data[key] += value
        return BudgetState(**data)


EventKind = Literal[
    "session_started",
    "model_turn",
    "tool_call",
    "tool_result",
    "compaction",
    "recovery",
    "budget_exhausted",
    "candidate_frozen",
    "model_failure",
]


class SolveOutcome(Strict):
    """Terminal result of a solve session. Infrastructure problems never produce one."""

    status: Literal["candidate_frozen", "model_failure"]
    reason: str
    validity: Literal["valid", "contract_invalid", "none"]
    candidate_digest: str | None = None
    candidate_artifact_id: str | None = None
    budget_exhausted: str | None = None
    frozen_after_exhaustion: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class ContextBudgetExceeded(SolveError):
    """The required core context cannot fit the model window: a declared model-side failure."""

    code = "CONTEXT_BUDGET_EXCEEDED"
    status_code = 422
