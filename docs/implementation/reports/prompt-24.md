Prompt 24 / Phase 5 — DONE (part 1 of WP-20)

1. Implemented functionality and changed files
   - Implemented the SWE-bench-style repository-repair suite adapter: `plugins/suites/swebench/`
     with four modules — `records.py` (methodology record, native task instance/test spec/draft,
     leakage checks, patch-path enforcement), `adapter.py` (T 17.1 `SuiteAdapter`: source-manifest
     import, methodology validation, frozen family protocol, evaluation plan, patch output),
     `grading.py` (the pinned upstream evaluator call, evaluator digest, run identity, candidate-bound
     cache) and `overlay.py` (protected grading overlay, candidate workspace, frozen-test overlay,
     grading run).
   - Registered the package as `plugins/suites/swebench` in the uv workspace, pinned the upstream
     evaluator to `swebench==5.0.2`, and registered the suite entry point.
   - Two fixtures, both generated from real source trees so snapshot, gold patch and test patch
     cannot drift: `pcb-native-compatible-calc` (authored, native record shape and native
     fail-to-pass/pass-to-pass metric) and `pcb-adapted-calc-rs` (a Rust port declaring two protocol
     deviations). Each carries reference, alternative, faulty and no-op candidates.
   - Evidence generator `plugins/suites/swebench/scripts/prompt24_evidence.py` and the E2E-36
     scenario `plugins/suites/swebench/tests/test_e2e36_native_repo_repair.py` (23 tests).
   - Main paths: `plugins/suites/swebench/src/polycodebench_suites_swebench/{records,adapter,grading,overlay}.py`,
     `plugins/suites/swebench/tests/{fixture_support,native_compatible_instance,adapted_port_instance}.py`,
     `tests/conftest.py`, `pyproject.toml`, `docs/implementation/{decisions.md,tickets.md,e2e-matrix.md,requirements-matrix.md,phase-map.md,progress.json}`.

2. Tests/commands actually run and their results
   - `uv run --locked --group dev pytest -q plugins/suites/swebench/tests -p no:cacheprovider`: PASS,
     22 passed, 1 skipped (the Rust port's image-backed test is gated on `PCB_TEST_DOCKER=1`).
   - `PCB_TEST_DOCKER=1 uv run --locked --group dev pytest -q plugins/suites/swebench/tests
     -p no:cacheprovider`: PASS, 23 passed. These are real executions: each candidate patch is
     applied, the hidden test patch is overlaid, the task's own frozen test command runs - inside
     the pinned Rust image by digest for the port, so it is compiled rather than described - and
     the pinned upstream `get_eval_report` grades the resulting log.
   - `uv run --locked --group dev python plugins/suites/swebench/scripts/prompt24_evidence.py`:
     PASS; wrote `docs/implementation/evidence/prompt-24-e2e36.json` from that run. Reference and
     alternative resolve `RESOLVED_FULL` on both fixtures (fail_to_pass 1/1, pass_to_pass 2/2 and
     1/1); the faulty partial fix and the no-op resolve `RESOLVED_NO` with the maintenance counts
     intact.
   - `uv run --locked --group dev ruff check plugins/suites`: PASS. `ruff format --check plugins/suites`:
     PASS. `uv run --locked --group dev mypy plugins/suites/swebench/src`: PASS, 7 source files,
     strict mode.
   - Not run: an official SWE-bench dataset import (no rights-cleared data exists, so there is
     nothing to import); a production-worker grading run (the port executes in the local Docker
     driver, which is development-tier evidence); the full workspace suite (unrelated
     language/C/Go admission work is active in this tree and its failures are not Prompt 24's).

3. Acceptance gates
   - Satisfied: PCB-24-1 (import/validation, immutable snapshot, patch output, recorded source
     revision and protocol differences, leakage refused). PCB-24-2 (native metric from the pinned
     upstream evaluator, kept separate from gate and quality evidence). PCB-24-3 (run identity binds
     task, candidate and evaluator digests; protected overlay overwrites graded-test edits). PCB-24-4
     (native-compatible and adapted/ported fixtures admitted through the actual harness with
     differing, correct labels, every outcome traceable to one frozen candidate).
   - E2E-36: PASS at the local-fixture tier. Recorded limits are listed above and in the evidence file.
   - WP-20 remains partial: Prompts 26–28 (self-repair, repository Q&A, prediction) are not started.

4. Decisions or specification discrepancies recorded
   - **D-24-01 — "native-compatible" is labelled `inspired`, not `native`.** Prompt 24's instruction
     ("unavailable/private datasets are explicit blockers rather than invented fixtures labeled
     official") and E2E-36's evidence column ("Native/adapted labels") pull in opposite directions.
     Both methodology documents (A §2 and `docs/methodology/swebench.md`) label authored
     pattern-following tasks `inspired`, and `methodology_label` is the only provenance field on the
     public `visible-manifest.json`, so a `native`-labelled authored fixture would read as official
     data. The label therefore follows provenance, not shape, and is enforced fail-closed:
     `MethodologyRecord` refuses `native` without an upstream source URL *and* a pinned revision, and
     `validate_methodology` refuses an `inspired` record carrying an upstream URL. An official
     manifest cannot import an authored fixture, and vice versa. The deviations register already
     permits all three labels for `swebench`.
   - **D-24-02 — the native metric comes from the pinned upstream evaluator, or from nothing.**
     `native_metrics` accepts the upstream result object and never recomputes a fraction; the upstream
     package is pinned to an exact revision and a mismatch raises rather than degrading, because the
     resolution rule is version-specific (skip semantics, the suite-ran guard, the exit-code
     cross-check). The evaluator digest covers the revision and the upstream entry points called, so
     an upstream change invalidates cached grades.
   - **A port's expected-test ids and test command follow its parser, not the Python fixture's.**
     `parse_log_cargo` reports bare test names, so the Rust fixture's lists are bare names;
     path-qualified ids would have been scored unresolved. `cargo test --quiet` prints dots, which
     the same parser cannot read, so the frozen command carries the repository's own
     `--no-fail-fast` and single-threaded-test convention and no `--quiet`. Both were found by
     running the port for real: a fully passing suite that scored unresolved, and an alternative
     that did not compile. Each is a real constraint of "wrap the upstream evaluator" - a fixture
     convenience that would silently grade the wrong thing.

5. Exact next command or numbered prompt
   - Next: Auxiliary R1 — execute Prompts 26–28 to close WP-20 (self-repair, repository Q&A, output
     and test-output prediction). Prompt 24's only remaining external blocker is official dataset
     rights for a `native`-labelled instance; the import path is implemented and source-identity
     checked, but has nothing to import.
