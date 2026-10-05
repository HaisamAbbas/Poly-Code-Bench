# Prompt 09 / Phase 2 — DONE (fixture model, development sandbox)

## 1. Implemented functionality and changed files
- Protocols (`config/protocols/single-shot-v1.yaml`, `standard-agent-v1.yaml`), effective-protocol digests, tool schemas/contracts, bounded context policy, deterministic model-free extraction: `packages/core/.../solve_contracts.py`, `solve_context.py`, `solve_extraction.py`, `solve_prompts.py`; `packages/services/.../solve_protocols.py`.
- Guest tools (`list_files`, `read_file`, `search`, `apply_patch`, `run_command`, `run_public_tests`): `packages/runner/.../guest_helper.py`, `guest_tools.py`; guest-side snapshots in `provider.py`.
- Event-sourced transcript + atomic workspace/transcript checkpoints: `packages/persistence/.../solve_state.py`, migration `b2f6d4a91c73`, `models.py`.
- Sessions, tool runner, executor, loader, inspection CLI: `packages/orchestration/.../solve/`; gateway `check_compatibility`/`provider_seed`.
- Tests: `tests/test_solve_*.py`, `tests/solve_support.py`, `tests/fixtures/solve_extraction/cases.json`; docs `docs/solve-sessions.md`; ledgers updated.

## 2. Tests and commands run
Full suite: 390 passed, 5 skipped, 0 failed (real Postgres, object store, local Docker). ruff format/check, strict mypy on the new modules (51 files), `check_boundaries.py`, `verify_prompt00.py`, `uv build --all-packages --offline`: pass. See `commands.md`.

## 3. Acceptance gates
- Satisfied (fixture model, dev sandbox): PCB-09-1..4, E2E-13, E2E-14 (tool execution, interrupted command checkpoints, multiple calls, invalid schemas, truncation, forbidden paths, exhausted budgets).
- Pending: live model behaviour and provider tool-call conventions (Prompt 17); production isolation (Prompt 06, owner-deferred).

## 4. Decisions and discrepancies
D-09-01..12 in `decisions.md`. Independent review produced 15 findings; the historical report identified finding 15's `budget_profile` limitation as open. Notable dispositions: gateway spending-limit refusal is now an operator interruption (resumable), not a model budget exhaustion; helper death with a live sandbox is a tool failure; workspace-limit breaches are recorded model failures; extraction never raises on model text. Another session committed Prompt 08; I edited `tests/test_jobs_postgres.py` only to scope a count to the attempt. No commits made at the time of the original report.

## Audit correction — 2026-10-05

D-09-12 is resolved. `DatabaseAssignmentLoader` now fails closed unless the run names an installed
budget profile whose complete solve limits exactly match the selected protocol. The proposed
budget document now contains exact profiles for all four installed protocols (single-shot,
standard-agent, prediction and repo-QA); these profiles do not authorize live spend. Tests verify
all four profile-to-protocol matches and reject missing or mismatched profiles.
`tests/test_solve_loader.py` reported 2 passed and 5 database-backed cases skipped because
`PCB_TEST_DATABASE_URL` is not configured. The historical Prompt 09 full-suite result above is
unchanged.

## 5. Next
Next: Prompt 10 — Implement Python support.
