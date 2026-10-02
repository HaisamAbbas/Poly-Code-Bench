Prompt 16 / Phase 2 — DONE

1. Implemented functionality and changed files
   - Added versioned metric, cohort, observation, aggregate and uncertainty contracts. Aggregation follows sample → task → frozen stratum → equal language weights; failures remain in the all-attempt denominator, missing infrastructure/task/language coverage makes the result unavailable, and conditional-on-pass metrics carry a distinct ID and pass denominator.
   - Added fixed-seed cluster/hierarchical percentile bootstrap, independent attempt resampling for each repeated cluster draw, paired comparisons, full-precision replicate evidence and common post-cutoff filtering. Added Scorecard-to-observation conversion.
   - Added synthetic/internal report and replay CLI, nine generated JSON schemas, reviewed release state transitions, exact-content approval invalidation, immutable correction successors, projection allowlists, Ed25519 signing/verification and transactional compare-and-swap pointers. Optional editorial index stays disabled.
   - Changed: `packages/publication/`, `packages/publication/pyproject.toml`, `scripts/pcb.py`, `scripts/export_publication_schemas.py`, `schemas/publication/`, `uv.lock`, `tests/test_publication_aggregation.py`, `tests/test_publication_reporting.py`, `tests/test_publication_releases.py`, `tests/test_releases.py`, `tests/test_uncertainty.py`, and Prompt 16 implementation ledgers/report.

2. Tests/commands actually run and their results
   - `.venv\Scripts\python.exe -m pytest tests/test_publication_aggregation.py tests/test_publication_reporting.py tests/test_publication_releases.py tests/test_releases.py tests/test_uncertainty.py -q -p no:cacheprovider --tb=short` — PASS, 23 tests, including CLI permission exit-code behavior.
   - `.venv\Scripts\python.exe -m ruff format --check ...` and `.venv\Scripts\python.exe -m ruff check ...` on the Prompt 16 package, CLI, schema exporter and tests — PASS.
   - `$env:MYPYPATH='packages/core/src;packages/scoring/src;packages/publication/src'; .venv\Scripts\python.exe -m mypy packages/publication/src/polycodebench_publication` — PASS, 5 source files.
   - `.venv\Scripts\python.exe scripts/export_publication_schemas.py --check` — PASS, 9 schemas.
   - `.venv\Scripts\python.exe scripts/check_boundaries.py`; `.venv\Scripts\python.exe scripts/pcb.py release --help`; `git diff --check` — PASS.
   - `uv lock; uv sync --all-packages --locked` — PASS; pinned `cryptography==46.0.5` installed. `python docs/implementation/verify_prompt00.py` passed before the final Prompt 16 ledger edits; it must be rerun after final edits.
   - Full integrated E2E-28 public comparison route and E2E-30 production target were not run: public API/deployment belong to Prompts 29/33. No real model results, human approvals, public release or external deployment were produced.

3. Acceptance gates
   - Satisfied: PCB-16-1, PCB-16-2, PCB-16-3 and PCB-16-4 at local synthetic/internal test scope. Evidence: `tests/test_publication_aggregation.py`, `tests/test_publication_reporting.py`, `tests/test_publication_releases.py`, `tests/test_releases.py`, `tests/test_uncertainty.py`.
   - Satisfied subcases: E2E-28 fixed common cohort, unknown cutoff and missing coverage; E2E-29 fixed-seed replay, correlated cluster resampling and paired metric differences; E2E-30 approval invalidation, concurrent pointer race, immutable correction, signing and safe projection. Detailed scope is recorded in `docs/implementation/e2e-matrix.md`.
   - Pending: full E2E-28 public comparison/API variants (Prompt 29); production publication and deployment variants of E2E-30 (Prompt 33); remaining Prompts 13–15 integration prerequisites; ranked scientific coverage and real pilot evidence. Synthetic fixtures are explicitly not benchmark results.
   - Blocked: no Prompt 16 implementation gate. External publication remains unauthorized because no target/review was supplied; this prompt only prepared local publication behavior.

4. Decisions or specification discrepancies recorded
   - D-16-01: local release state uses SQLite transactions and compare-and-swap; Ed25519 key is supplied outside workers; release CLI role flags are local administration only. Public deployment needs its separately authorized target. No specification discrepancy.

5. Exact next command or numbered prompt
   - Next: Prompt 17 — Run and verify the real Python/Rust pilot.
