Prompt 28 / Phase 5 exit — DONE (prediction suites), E2E-38 PARTIAL

1. Implemented functionality and changed files
   - Deterministic prediction contracts: `plugins/suites/swebench/src/polycodebench_suites_swebench/prediction.py`
     (frozen `NormalizationRules` for `exact_bytes`/`normalized_text`/`typed_json`, the
     execution-tool refusal, `check_protocol_constraints`, one-pass `parse_submission`, total
     comparison, answer-only metric definitions) and `prediction_grading.py` (`grade_prediction_task`
     with candidate/oracle digest binding, plus `audit_track_b`).
   - The frozen prediction cohort: `config/protocols/prediction-v1.yaml` — zero tools, zero tool
     calls, one model turn, no test/hidden feedback, network disabled. The evaluated model cannot
     run the target because the protocol has no execution tool; `validate_prediction_tools` refuses
     every such tool by name, so a different tool policy is a different cohort.
   - `pcb-prediction-v1` prompt policy: `packages/core/src/polycodebench_core/prediction_prompts.py`
     — deterministic rendering of a prediction run's system and task messages.
   - Two admitted task packs: `taskpacks/prediction/output-prediction` (`pcb-output-prediction-ops`,
     family `output_prediction`, label `adapted`, submission `text`) and
     `taskpacks/prediction/test-prediction` (`pcb-test-prediction-clamp`, family `test_prediction`,
     label `adapted`, submission `typed_json`). Both import through the real `TaskPackageImporter`;
     the importer's disclosure scan caught and rejected a first draft that leaked oracle-adjacent
     bytes, which is what made the admission real rather than claimed.
   - Track B coverage audit: `prediction_grading.audit_track_b` walks all seven required families
     through their real grader entrypoints, refuses a verdict that omits a family, and fails a
     family whose pack or evidence path is absent. `docs/implementation/reports/suite-coverage.md`
     records the per-family table and the scope limits.
   - Evidence generator: `plugins/suites/swebench/scripts/prompt28_evidence.py`.

2. Tests/commands actually run and their results
   - `uv run --locked --group dev pytest -q plugins/suites/swebench/tests -p no:cacheprovider`:
     PASS, 43 passed, 1 skipped (the Rust port's image-backed test remains gated on
     `PCB_TEST_DOCKER=1`). 21 of those tests are new E2E-38 prediction tests.
   - `uv run --locked --group dev python plugins/suites/swebench/scripts/prompt28_evidence.py`:
     PASS; wrote `evidence/prompt-28-e2e38.json` from the run — 16 graded prediction cases, the
     protocol-restriction demonstration, the answer-only metric definitions and the coverage audit
     (`wp20_closed: true`, `failed_families: []`).
   - `uv run --locked --group dev ruff check packages/core plugins/suites`: PASS.
     `ruff format`: PASS. `uv run --locked --group dev mypy plugins/suites/swebench/src`: PASS,
     9 source files, strict. `mypy` on `prediction_prompts.py`: PASS.
   - `TaskPackageImporter.import_package` on both prediction packs: PASS (pack digests recorded in
     the evidence document). The importer's disclosure scan rejected the first test-prediction
     draft; the fixture was corrected and re-admitted.
   - Not run: a live model (no provisioning — the graded submissions are the fixtures' authored
     answers, so the evidence shows the grader, not model skill); a production worker; Prompt 27's
     Q&A (PCB-27 is still `not_started`, so the `repo_qa` evidence pointer is provisional).

3. Acceptance gates
   - Satisfied: PCB-28-1 (structural execution restriction; different tool policy = different
     cohort), PCB-28-2 (three frozen normalization modes; parse errors are wrong answers; no judge
     in the path), PCB-28-3 (prediction fixtures admitted; methodology `adapted` per the register;
     answer-only metrics with the six code dimensions absent rather than zero), PCB-28-4 (the
     seven-family audit closes WP-20 on real entrypoints; no required Track B scenario is missing).
   - E2E-38: PARTIAL — the prediction half passes at the local fixture tier; the Q&A half needs
     Prompt 27, which is not started.
   - E2E-36 (Prompt 24) and E2E-37 (Prompt 26) are reused as recorded; no new run of them is
     claimed.
   - WP-20: closed by the audit with one recorded caveat — `repo_qa`'s evidence pointer is
     provisional until Prompt 27 completes.

4. Decisions or specification discrepancies recorded
   - **D-28-01 — prediction normalization is frozen, parse-failed and unrescuable.** Each rule of
     T 17.4 is a checkable boundary: a rules document that contradicts its mode is refused (the
     grader can never default), parsing happens once before comparison, and the grade report has
     no judge field at all, so a mismatch cannot be softened. The same contract carries the
     execution restriction.
   - **The self-repair family has no grader of its own, and the audit says so.** Its final round is
     a frozen candidate that the standard evaluation stage grades; the audit points at
     `SuiteAdmission.admit` rather than at a module that would have to be invented. A summary that
     claimed a dedicated repair grader would have been false.
   - **`repo_qa`'s evidence pointer is provisional.** Prompt 27's Q&A modules exist in the tree
     uncommitted while this prompt ran; the audit imports them as they stand and does not commit
     or claim that work. `evidence/prompt-28-e2e38.json` records this in `not_claimed`.

5. Exact next command or numbered prompt
   - Next: Prompt 29 — Implement the complete public API and projections (E2E-25/26/28/39 API
     variants). Before then: Prompt 27 must complete so `repo_qa`'s evidence pointer can become a
     dedicated Q&A artifact and E2E-38's Q&A half can run.

## Audit correction (2026-10-05)

The repository audit found that the original `audit_track_b` treated non-empty path strings as
proof. It also pointed `repo_qa` at a Python source file and reused Prompt 26 evidence. That did not
verify task admission or family evidence. The audit now resolves repository-contained paths,
validates task-package family identity and fixture files, and checks family-specific evidence
contents against the pack. `repo_qa` now uses `taskpacks/qa/py-configkit-qa-v1` and its dedicated
Prompt 27 E2E-38 fixture record. This supersedes the provisional `repo_qa` pointer and dependency
note in Prompt 28's original closing section.

The corrected audit passes all seven family checks. The regenerated
`docs/implementation/evidence/prompt-28-e2e38.json` records `wp20_closed: true` and no failed
families. The full SWE-bench suite passed 44 tests; one Rust image test remains opt-in under
`PCB_TEST_DOCKER=1`. Ruff and strict mypy passed. These are authored local-fixture/admission records;
they do not claim live model skill, live judge review, production-worker execution or quality
admission.
