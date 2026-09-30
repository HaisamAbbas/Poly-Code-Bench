# PolyCodeBench implementation execution contract

Copied verbatim from `PolyCodeBench-Codex-End-to-End-Prompt-Pack-v1.md` sections 4-5. Those sections remain authoritative.

## 4. Execution contract: applies to every prompt

Prompt 00 must place this contract in `docs/implementation/execution-contract.md`, preserve its requirements, and link this pack. Later prompts read that file first.

### 4.1 Work behavior

1. Read applicable `AGENTS.md`/repository instructions and inspect the current working tree. Preserve unrelated changes. Do not reset, force-push, replace the repository, or delete working modules merely to follow this pack’s suggested structure.
2. Resolve actual source-document paths in Prompt 00. Read the named sections and relevant contracts for each prompt. Reinspect changed source files when their digest changes. Do not work from this pack’s summary alone.
3. Implement the requested behavior in the current repository. Plans, stubs, mocked screenshots, TODOs, and generated interfaces without integrated behavior are not completion.
4. Use existing correct implementations when they satisfy the spec; write a gap map before major refactors. Do not assume this is a different benchmark or an unrelated prior project.
5. Select routine implementation details autonomously. Record them in an ADR only when they affect architecture, public contracts, interpretation of scores, security, operations, or maintenance meaningfully. Do not turn every small choice into an approval request.
6. Respect actual tool/environment restrictions. If a required action is rejected, report the rejected action and reason; never suggest disabling the control. Complete safe, independent work first.
7. Local/reversible coding and ordinary verification are authorized by the prompt. Paid provider calls/cloud provisioning/publication must stay inside an explicitly authorized target, budget and scope. Reuse existing authorization; where it is absent, prepare the exact plan/config first and identify the missing authorization as the final blocker.
8. Do not send messages, open public issues/PRs, or publish results solely because a prompt creates local changes. Use repository/user authorization for external writes.
9. Run relevant checks, fix failures, and repeat only what the change or remaining risk requires. Do not rerun expensive live campaigns reflexively.
10. Stop after the current numbered prompt and its report. Supplying the next numbered prompt is the handoff; do not ask the user to approve work already authorized within the current prompt.

### 4.2 Truthful evidence

- Preserve REQ, WP and E2E IDs. Every implemented ticket links to requirements, changed paths and appropriate verification.
- Distinguish fixture, unit, integration, browser, real sandbox, live provider, human review, and production-shaped evidence. A passing fake transport does not satisfy E2E-31.
- “Test written” and “test passed” are different. “Code implemented” and “phase accepted” are different. Missing credentials, data rights, human judgments or hardware remain explicit blockers.
- Some E2E scenarios span several prompts. Mark the tested subcase and environment; keep the full scenario pending until all its required variants have run. Do not mark E2E-25/26/39 complete because one early route passed.
- Do not invent benchmark outcomes, provider usage, pricing, confidence intervals, source licenses, tool capabilities or human approvals.
- Keep model failures in the denominator, gate quality after correctness failure, preserve unknowns/N/A, and separate generation from evaluation retries.
- Never weaken acceptance criteria, skip a required analyzer, raise budgets, expose hidden data, change cohort weights or substitute an easier task to make a gate pass.
- A specification discrepancy is recorded with evidence, affected clauses, proposed resolution and scope. For nonbreaking implementation clarifications choose the narrowest compatible interpretation. For material product/scoring/security changes, prepare a concrete amendment and leave the affected gate pending for the owner’s decision.

### 4.3 Persistent implementation ledger

Maintain these artifacts inside the repository, adapting paths once if existing conventions require it:

| Artifact | Required content |
|---|---|
| `docs/implementation/source-manifest.json` | Actual source paths, versions and hashes |
| `docs/implementation/phase-map.md` | Prompt/phase sequence and phase gate state |
| `docs/implementation/tickets.md` | Every PCB ticket with owner prompt, dependencies, implementation status, verification status, acceptance criteria and evidence |
| `docs/implementation/requirements-matrix.md` | REQ-01–14 → WP/tickets → code paths → E2E evidence |
| `docs/implementation/e2e-matrix.md` | E2E-01–43 and required variants/environments, actual status, commands and artifacts |
| `docs/implementation/decisions.md` | ADR/discrepancy index, including explicit “none” when appropriate |
| `docs/implementation/commands.md` | Verified setup/test/run/recovery commands for this actual repository |
| `docs/implementation/reports/prompt-NN.md` | Required brief report for each prompt |
| `docs/implementation/reports/phase-N.md` | Consolidated report at each phase end |
| `docs/implementation/progress.json` | Last completed prompt, active scope, blockers, source version, evidence summary and exact next action |

Store detailed test logs and run artifacts in the repository’s appropriate test/report/artifact system, not large blobs in the brief report. Redact secrets and held-out material. Do not commit private task data or raw credentials. Use git commit IDs when available; otherwise record the actual working-tree state rather than inventing a commit.

Implementation state: `not_started`, `in_progress`, `implemented`. Verification state: `not_run`, `passed`, `failed`, `blocked`. A ticket is **DONE** only when implemented and all its required verification passes. An incomplete external gate cannot be labeled DONE merely because code exists.

If a dependency is blocked, implement any independently verifiable work within the authorized prompt, but keep dependent gates blocked. The next action is the unblock/resume step, not an instruction to pretend the phase completed. If the user later explicitly requests an independent later prompt, work on that scope while preserving the unmet dependency; do not silently waive it.

### 4.4 Global definition of done

A prompt is complete when its requested behavior is integrated through actual entrypoints; source contracts are satisfied; targeted tests actually pass; required live/human/infrastructure evidence exists where specified; all changed paths and evidence are recorded; relevant documentation and matrices agree; and no required ticket/gate remains failed, pending or blocked.

A phase is complete only when all of its prompt gates and the phase-wide integration gate pass. A public ranked release has additional scientific coverage/calibration conditions; completing website code or the exploratory pilot does not satisfy them.

## 5. Required completion report

**Every prompt and every phase must end with this information, briefly.** Use the exact structure below in the final response and corresponding report file. The short response may link to detailed evidence. Do not replace it with “all done.”

```text
Prompt NN / Phase N — DONE | PARTIAL | BLOCKED

1. Implemented functionality and changed files
   - Behavior implemented; relevant changed paths.

2. Tests/commands actually run and their results
   - Exact command; environment; PASS/FAIL; result or evidence path.
   - Explicitly list required checks not run and why.

3. Acceptance gates
   - Satisfied: ticket/gate/E2E IDs and evidence.
   - Pending: IDs and unfinished work/verification.
   - Blocked: IDs, concrete cause, and required input/action.

4. Decisions or specification discrepancies recorded
   - Decision/discrepancy ID and chosen/proposed resolution; or None.

5. Exact next command or numbered prompt
   - If DONE: “Next: Prompt NN — <title>.”
   - Otherwise: one exact available command, or “Next: Auxiliary R1,”
     with the recorded blocker/re-entry scope. Do not invent a command.
```

At phase boundaries, item 3 includes the aggregate phase gate. “No tests needed” must be justified by the scope; documentation-only Prompt 00 can report document/consistency checks instead of application tests. A command line is “actually run” only if it was executed during this work or its prior evidence is explicitly identified as prior/reused and still applicable.
