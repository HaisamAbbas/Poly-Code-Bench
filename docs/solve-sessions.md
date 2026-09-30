# Solve sessions (Prompt 09)

A solve session turns one attempt into a frozen candidate through the model gateway and, for the
agent protocol, a sandbox. Model-side outcomes (a wrong, invalid or absent answer; an exhausted
budget) are results. Infrastructure problems (guest died, lease lost, provider unreachable) raise
and are resumed; they never become a candidate or a model failure.

## Protocols

`config/protocols/*.yaml` are frozen `SolveProtocol` documents. Their canonical digest is the
protocol identity. At run time a task's `protocol_constraints` are intersected with the protocol
(tools, turn/tool/wall ceilings — minimum of the two; a task can narrow, never widen), producing an
`EffectiveProtocol` whose digest is recorded in the `session_started` event, every checkpoint and
the candidate row. A checkpoint written under a different identity is refused on resume. Tasks
that require hidden feedback are not solvable by these protocols.

| | `single-shot-v1` | `standard-agent-v1` |
|---|---|---|
| Model turns / tool calls | 1 / 0 | 30 / 100 |
| Active solve time | 180 s | 600 s (60 s per command) |
| Input / output tokens | 32,000 / 8,000 | 250,000 / 30,000 |
| Tools | none | `list_files`, `read_file`, `search`, `apply_patch`, `run_command`, `run_public_tests` |
| Per-request context ceiling | 32,000 est. tokens | 60,000 est. tokens, latest 8 turns in full |
| Final reply | declared extraction rule | no tool call; files/patch tasks freeze the workspace |

Budgets mirror `config/budgets/pilot-v1.yaml` (proposed, not authorised for live spend).
`pcb-solve protocols` prints the installed protocols and digests.

## Loop and commits

```text
restore checkpoint (or stage the visible workspace) → for each turn: build prompt from committed
state → gateway.call(turn-N) → commit model_turn + checkpoint → for each tool call in provider
order: commit tool_started (mutating tools) → run in guest → sweep descendants → snapshot →
commit tool_result + checkpoint → final reply / exhaustion → freeze candidate → terminal event
```

Every commit is one PostgreSQL transaction: a row lock on the attempt, a compare-and-swap on the
last event sequence, the new `attempt_event` rows, and the `attempt_checkpoint` covering them.
A checkpoint names a workspace archive (tar, content-addressed), a transcript manifest (the event
list with digests, budget, pending call IDs, file modes) and the protocol digest, bound together
by a digest that restoration re-verifies. Restoration also re-derives the budget from the events
and compares it with the checkpoint; any mismatch raises `CheckpointMismatch`.

## Recovery

| Failure boundary | Behaviour |
|---|---|
| Gateway recorded turn N, controller died before committing it | The same logical key `turn-N` returns the stored response (`source: stored`); no new request. The prompt is a pure function of committed state, so the request digest is identical. |
| Guest died during a command | `tool_started` without `tool_result`. A new guest receives the checkpointed workspace (bytes and permission bits); only that command (and later ones of the turn) run, the repeat is recorded (`recovery` event, `replayed: true`, `repeated_compute_ms`). Committed results are never re-run. |
| Terminal event already committed | Re-running returns the same result and makes no request. |
| Candidate row exists, terminal event missing | The next run records the terminal event; candidate bytes are immutable (digest conflict is refused). |
| Stale or cancelled controller | The sequence CAS and the attempt-state check fail the commit. |

## Tools

All arguments are strict schemas (`solve_contracts.ARGS_MODELS`); unknown fields and type
coercions are errors. Invalid or unavailable calls return a typed error to the model and consume
the tool-call budget, but never grant new permissions. Paths are normalised on the host and
re-checked in the guest (no absolute paths, `..`, backslashes, control characters, symlinks, or
`.pcb_*` harness names). The guest helper (`runner/guest_helper.py`) is passed in the argument
vector on every call, so workspace files cannot replace it. Results carry `event_seq`,
`tool_call_id`, `status`, `truncated`, `original_bytes`, `returned_bytes`, `artifact_id` and
`budget_remaining`; raw command output is archived (capped) and only a bounded view enters the
prompt.

* Commands run as `/bin/sh -c` **inside** the guest in their own session, with a minimal
  environment and no network. After each command the helper (a child subreaper) kills every
  descendant and reaps zombies; the controller sweeps again before snapshotting.
* `apply_patch` parses unified diffs itself (exact context, nearest-offset matching, create and
  delete, no renames/modes/binary), validates every file before writing anything, and rolls back
  if a write fails.
* `run_public_tests` runs groups declared in the visible bundle's `public-tests.json`
  (`id`, `argv`, `cwd`, `timeout_seconds`). The command is fixed by the task, not the model, and
  the file is protected.
* Symlinks and special files a command creates are deleted before a snapshot and listed in the
  event (v1 rejects submitted links).

## Extraction and freezing

`solve_extraction` is pure and deterministic; it never consults a model. Single-shot accepts the
declared JSON envelope (whole response must be one JSON object, no duplicate keys) or, for a
single-file output, exactly one fenced block — two blocks are `ambiguous_multiple_blocks`, never
resolved. Agents on `files`/`patch` tasks freeze the declared paths from the workspace as written
(patch tasks freeze a unified diff of changes inside the output contract; other changes are
recorded, not submitted). A modified or deleted protected file makes the candidate
`contract_invalid`. Findings beyond the limit are invalid, never trimmed, and a separately valid
patch stays evaluable.

## Budgets

The tool-call budget answers overflow calls with `budget_exhausted` errors and then stops;
exhausting turns, tokens, active time, the gateway's spending limit or the context ceiling ends
the session. Where the protocol permits (`freeze_on_budget_exhaustion`) and the task has a
workspace, the current workspace is frozen (valid → `candidate_frozen`, otherwise a recorded
model failure); answer-style tasks have nothing to freeze. Unknown provider usage is charged at the
request's conservative bound, never zero.

## Operating

* `pcb-solve inspect <attempt-id>` — events by kind, turns (source, usage, estimate vs reported
  input tokens, context record), tool calls, recoveries, exhaustion, terminal outcome, candidate,
  checkpoint and consumed budget (needs `PCB_DATABASE_URL`, `PCB_OBJECT_STORE_ENDPOINT`,
  `PCB_BUCKET_*`).
* `SolveStageExecutor` plugs into `WorkerService`; `DatabaseAssignmentLoader` builds the assignment
  from the frozen task version, the run's resolved model config and the visible bundle only.

## Known limits

* Docker mounts the local workspace `noexec`; scripts run through an interpreter. Language
  prompts that build native binaries need a different execution policy (Prompt 10+).
* `workspace_patch` requires UTF-8 text; binary changes make a patch candidate invalid.
* The model is a fixture in every test; live behaviour is Prompt 17's gate.
