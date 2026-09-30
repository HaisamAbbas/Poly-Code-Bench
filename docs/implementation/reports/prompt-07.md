# Prompt 07 completion report

## Independent review and fixes

Reviewed against `Constitution/production_quality_software_engineering_prompt-3.md` and the Prompt 07 acceptance criteria. The original verification below is retained as historical evidence. The corrected candidate was validated in an isolated Prompt 06/07 snapshot because another session is implementing Prompt 08; its files and shared model additions are excluded from this commit.

Confirmed defects and corrections:

- Concurrent selection snapshots could both reserve the last campaign/provider allowance. Claims now acquire fairness locks and recount before leasing, without waiting on busy scope locks.
- A failed/expired parent left its descendants blocked. Terminal infrastructure failure now propagates through the DAG. Unselected gate branches use the explicit `branch_not_selected` skip and do not become infrastructure failures. Dependency checks reject unknown gates consistently.
- Completion replay accepted a changed gate/model outcome with the same artifact, and caller event details could overwrite authoritative fields. Migration `8ac42e1d09bf` persists the complete result, replay checks its identity, and additional event fields are nested.
- Expiry freed a slot while guest creation was in flight; cleanup was not fenced to its delivery. Provisioning now reserves a busy slot before calling the provider, cleanup matches worker/job/fence, and dirty slots block reclaim. Unknown creation failures retain capacity until verified recovery.
- Worker supervision could stop silently after a database error, start heartbeats too late, leave child tasks/resources running after cancellation, or lose idle registration. Heartbeats cover provisioning, authority errors revoke execution, cancellation drains creation/execution tasks and verifies destruction, and idle workers renew registration. A foreign worker claim is rejected before repository access.
- Cancellation skipped locked jobs and evaluation heartbeats omitted parent run revocation. Cancellation waits for every scoped job, cascades to active evaluations, and parent revocation is checked at dispatch/heartbeat/commit. Scope transactions serialize concurrent DAG completion.
- Exhausted evaluation jobs did not retain an infrastructure classification. Evaluations now record `failure_class=infra_blocked` with a failed state. `pcb-scheduler reap --watch` implements the specified 30-second periodic reaper; command failures return sanitized exit codes.

Final verification: **124 tests passed, no skips, in 28.60 seconds**, including 20 PostgreSQL scheduler cases, 30 worker lifecycle tests, six CLI tests and the real Docker cancellation/containment tests. Ruff formatting (107 files) and lint passed. Broad mypy checked 56 source files; the separate sandbox/guest/lifecycle check passed for six files. Fresh database migration, previous-schema upgrade and drift checks passed; all ten packages built as source and wheel; compilation, operator commands, dependency boundaries, smoke/import checks, startup/contract schema checks and ledger/source checks passed.

Evidence and checklist: `docs/implementation/evidence/prompt-07-integration.json` and `docs/implementation/reports/prompt-07-review.md`. Prompt 07 remains **PARTIAL**: full E2E-09 model usage and ranked-release evidence still depend on Prompts 08/17. Production VM evidence and hosted CI were not run. These limitations do not authorize fabricated usage or a production isolation claim.

Prompt 07 / Phase 2 — PARTIAL

1. Implemented functionality and changed files
   - Added typed DAG/job/lease contracts, transactional stage graph creation and unblocking, gate-specific skips, PostgreSQL `SKIP LOCKED` claims, 120-second leases, 30-second heartbeats, fencing, execution records, idempotent result commits, capacity slots, fairness caps, bounded retry/reaping, append-only events, cancellation/revocation and worker lifecycle integration. Added operator commands `pcb-scheduler reap`, `pcb-scheduler worker-status`, and `pcb-scheduler cancel-attempt`.
   - Key implementation: `packages/core/src/polycodebench_core/jobs.py`, `packages/persistence/src/polycodebench_persistence/jobs.py`, `packages/persistence/src/polycodebench_persistence/models.py`, migrations `f17b6b04a237` and `7b8cc92d13ea`, `packages/orchestration/src/polycodebench_orchestration/worker.py`, and `packages/orchestration/src/polycodebench_orchestration/cli.py`.
   - Added actual PostgreSQL integration coverage in `tests/test_jobs_postgres.py`; updated schema permission grants, package boundaries, lockfile, CI and implementation ledgers. Detailed local evidence is `docs/implementation/evidence/prompt-07-integration.json`.

2. Tests/commands actually run and their results
   - Applied both scheduler migrations to local PostgreSQL 17.6 and ran Alembic drift check: PASS, no schema drift. Applied scheduler grants to the isolated test database.
   - `uv run --locked --offline pytest -q -p no:cacheprovider --tb=short tests/test_jobs_postgres.py`: PASS, 7 tests against PostgreSQL 17.6, SeaweedFS 4.48 and local Docker Linux engine 29.7.2.
   - `uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short`: PASS, 75 tests in 24.35s.
   - Locked offline workspace sync, all 10 Python package builds, mypy (34 source files), Ruff, package-boundary/import/config smoke checks, Python compilation, CLI help and a live database reaper invocation passed. The source/ledger checker passed before the final verified-command registry clarification; final consistency checks are recorded in the current turn and must remain green.
   - Required E2E-07 and E2E-08 passed with two competing workers and injected lease, upload and commit-ack failures. E2E-09's live local Docker cancellation subcase passed. Full E2E-09 model usage retention and ranked-release exclusion were not run because the model/accounting and release paths belong to Prompts 08/17. Hosted CI and production VM lifecycle tests were not run; the latter remain owner-deferred for lack of cloud access.

3. Acceptance gates
   - Satisfied: PCB-07-1, PCB-07-2 and PCB-07-3; E2E-07 and E2E-08. Evidence covers branch-specific DAG routing, infrastructure retries distinct from successful wrong answers, competing claims, lease expiry/fencing, stale commit denial, same-output replay, artifact reuse after a precommit crash, retry bounds, capacity/fairness, cleanup confirmation and durable events.
   - Partial: PCB-07-4 and E2E-09. Local cancellation prevents further dispatch, retains completed evidence, terminates the active guest before slot reuse and does not classify unrun work as model failure. Incurred model usage and ranked-release exclusion await Prompt 08/17, so the full E2E-09 scenario remains `not_run`.
   - Phase 2 remains in progress. Prompt 06 production E2E-05/06 is separately deferred by the owner; local Docker results are development evidence and do not establish production isolation. No cloud calls, model calls, paid work, external uploads or release actions occurred.

4. Decisions/discrepancies and persisted state
   - D-07-01 narrows stage-execution immutability to permit one fenced, exactly-once finalization while keeping identity immutable. D-07-02 records configurable default campaign/provider fairness caps of 4/2. D-07-03 adds a distinct cancelled evaluation state. D-07-04 records the scoped E2E-09 local evidence and pending usage/release variants. D-07-05 updates the consistency checker so an authorized active prompt can follow an intervening prompt persisted as partial; Prompt 06 remains partial and unchanged.
   - `progress.json`, ticket/requirements/work-package/E2E matrices, decisions and command registry identify Prompt 07 as partial and retain the exact next action. Existing Prompt 06 and unrelated Constitution work were preserved.

5. Exact next command or numbered prompt
   - Execute Prompt 08 — Implement model adapters and budget accounting. Then resume Prompt 07 only to add durable model-call/usage retention evidence for E2E-09; separately resume Prompt 06 production E2E-05/06 when authorized cloud access and its recorded prerequisites are available. Do not start Prompt 09 before the required gates are resolved.
