# Prompt 12 — DONE (independent grading and normalized evidence; development sandbox)

## 1. Implemented functionality and changed files

- **Evaluation graph** — `packages/evaluation/src/polycodebench_evaluation/evaluator.py`.
  `Evaluator.evaluate()` executes the Technical Spec 12.1 sequence for any `ExecutableLanguagePlugin`:
  (1) candidate payload digest, task binding and output-contract `allowed_paths` validation, rejecting a
  submission before anything runs; (2) build in a **fresh grading guest per plan** through the existing
  `PlanRunner` (`lane="grading"`); (3) each acceptance/quality group run for its declared repetitions and
  reconciled against the *frozen oracle inventory*; (4) hard-acceptance failure emits failure evidence
  and gates all optional quality work off; (5) analyzers run only after the gate passes, on both the
  candidate and the baseline side; (6) canonical issue grouping, composite ownership, baseline relations
  and review items; (7) one frozen `EvaluationEvidence` manifest. Candidate bytes never enter the
  `overlay`/`config` pools — `materialize_inputs` fills them from trusted storage only.
- **Normalized evidence contract** — `packages/evaluation/src/polycodebench_evaluation/evidence.py`
  (strict frozen models): `ToolRecord` (name, version, image/lock/rule-bundle/advisory digests, parser
  version, scope), `AnalyzerEvidence` (supervised plan status, scan status, raw output digests),
  `CaseEvidence`, `ScenarioEvidence` (weight, repetitions, credit, hard/quality gate effect),
  `PropertyEvidence`, `IssueEvidence` (relation, single owner, `counted_once`, all reporting tools),
  `ReviewItem`, `ProfileItemEvidence`, `EvaluationEvidence` with a canonical `report_digest`.
- **Tests** — `tests/test_evaluator.py` (offline normalization rules), `tests/test_evaluator_docker.py`
  (real local-Docker acceptance: Python reference, Rust reference, E2E-17, E2E-18).
- **Ledger** — `decisions.md` D-12-01..D-12-09, `tickets.md` (PCB-12-1..4), `e2e-matrix.md`
  (E2E-04/16/17/18), `requirements-matrix.md` (REQ-04/05/07), `progress.json`, `commands.md`, this report.

### Defect found and fixed by real execution

| Defect | Fix |
|---|---|
| The first implementation grouped **already normalized** observations, so a duplicate scanner report lost the second tool's identity and a genuinely deduplicated issue looked like a single-tool finding (D-12-08) | The evaluator keeps every pre-normalization member for provenance (`IssueEvidence.tools`) while the normalized entry remains the primary for severity/owner |

## 2. Tests/commands run

- `pytest tests/test_evaluator.py` — PASS, 4 offline tests.
- `PCB_TEST_DOCKER=1 pytest tests/test_evaluator_docker.py tests/test_evaluator.py` — PASS, **8 tests in
  118s** with real Docker containers in the pinned Python/Rust images:
  - `test_evaluator_full_run_clean_reference` (E2E-04 evaluator-worker variant, Python): `gate: pass`,
    all group verdicts pass, robustness scenario `stream-not-materialised` `passed` with credit 10000,
    `robustness_score_bp: 10000`, Hypothesis evidence `derandomized` with pinned example count,
    `profile_complete: true`, digest-addressed raw artifacts.
  - `test_evaluator_rust_fixture_reference` (E2E-04 evaluator-worker variant, Rust): `gate: pass`,
    build pass, scenario `large-input` passed, clippy/context measured.
  - `test_e2e_17_candidate_edits_tests_or_prints_fake_success`: (a) a submission bundling a replacement
    `tests/test_acceptance.py` is rejected with `disallowed_paths` and **zero** analyzer runs; (b) a
    candidate printing `ALL TESTS PASSED (7/7)` with wrong results fails on the authoritative inventory
    (`failed_cases` non-empty) and `quality_work_gated_off:correctness_gate_not_pass`.
  - `test_e2e_18_dedup_composite_owner_and_baseline_debt`: four planted security defects produce exactly
    four composite issues, each naming **both** tools and one `security` owner; all preexisting debt is
    `unchanged_out_of_scope` (visible, not blamed).
- `python scripts/python_conformance.py --report docs/implementation/evidence/prompt-12-python-conformance.json`
  — PASS 14/14 (E2E-15/16 Python re-run).
- `python scripts/rust_conformance.py --report docs/implementation/evidence/prompt-12-rust-conformance.json`
  — PASS 16/16 across all seven categories (E2E-15/16 Rust, including the Miri unsupported/hang variants).
- `pytest tests -q -p no:cacheprovider` — PASS **445 passed, 143 skipped** (skips are PostgreSQL/Docker
  opt-ins this host did not enable).
- `ruff check` / `ruff format --check` on the touched paths — PASS. `mypy` on the touched packages adds no
  new errors beyond the repo's pre-existing `import-untyped` notes. `scripts/check_boundaries.py` and
  `docs/implementation/verify_prompt00.py` — PASS.
- **Not run:** production execution tier, sealed hidden lane, registry publication, paid providers — none
  were configured and no such action was taken. Evidence is development-tier only.

## 3. Acceptance gates

- **Satisfied:** PCB-12-1 (fresh grading guests, digest/allowed-path validation, trusted overlays and
  inventories; missing mandatory tests cannot pass — E2E-17), PCB-12-2 (supervised analyzer execution,
  raw report references, tool/rule/advisory versions, explicit failure semantics — E2E-16 Python and Rust),
  PCB-12-3 (baseline relations, semantic issue identity, reviewed cross-tool dedup — E2E-18),
  PCB-12-4 (weighted robustness scenarios, seeded property evidence, applicability, Python **and** Rust
  fixture integration — E2E-04 evaluator-worker variant). E2E-17 and E2E-18 are fully passed for
  Python/Rust code generation; the remaining families (24-28) stay pending, so those rows remain
  `not_run` overall in the matrix with the passed subcases recorded.
- **Pending (not claimed):** performance measurement (Prompt 13), judge anchors and human calibration
  (Prompt 14), pure scoring and replay (Prompt 15), production-worker execution (Prompt 06,
  owner-deferred), curator approval/task freeze and owner rights confirmation. Quality admission stays
  `pending`; profiles remain `effective_for_scoring: false`.
- **Blocked (external):** none newly introduced. Existing blockers are unchanged: production cloud
  isolation, hosted provider credentials, object-store IAM, publication target.

## 4. Decisions or discrepancies recorded

D-12-01 (evaluator is a separate graph from admission), D-12-02 (candidate bytes can never mint control
material; disallowed paths are rejected visibly, not ignored), D-12-03 (in-scope determined by whether
the candidate's file bytes differ from the baseline — the narrowest honest reading of "required repair
scope" for single-file tasks), D-12-04 (ambiguous mappings stay `unknown` + reviewable), D-12-05 (native
metrics separate from the stricter gate; a findings exit is a complete scan, only tool_error/timed_out/
output_missing are incomplete), D-12-06 (scenarios get full credit or nothing; incomplete → no score),
D-12-07 (property/fuzz identity is taken from what the harness actually ran), D-12-08 (duplicate reports
kept as provenance, counted once), D-12-09 (rejected submissions still produce evidence). No source
specification conflict was found; the two judgment calls (D-12-03, D-12-05) are recorded for owner
review and do not change scoring, which remains Prompt 15.

## 5. Next

Next: Prompt 13 — Implement performance measurement.