# Prompt 27 / Phase 5 — DONE

1. Implemented functionality and changed files
   - Repository Q&A as answer evaluation with repository evidence, not a six-dimensional score:
     `packages/core/src/polycodebench_core/qa_contracts.py` freezes `QaTaskInputs` (question,
     base digest, snapshot paths), the structured `QaAnswer` envelope (prose + claim-level
     citations), versioned `QaOracle`/`AtomicFact` (weights, accepted paraphrases, verifying
     spans tied to the base digest, exact values), the frozen `claim-extraction-v1` procedure,
     deterministic `contradiction-v1`, citation validity, the preserved `native_presence_aggregation`,
     the weighted fact-recall formula, and `RetrievalRecord` retrieval logging (context and
     truncation). `qa_prompts.py` (policy `pcb-qa-v1`) and `config/protocols/repo-qa-v1.yaml`
     make solving read/search-only: `validate_qa_protocol_tools` refuses every mutating tool, so
     editing is disabled at the contract level (PCB-27-1).
   - Fixed entailment judging with preserved native aggregation (PCB-27-2): fact credit is the
     mean of the fixed three 0/1 entailment votes or an adjudication; `entailment_votes` converts
     real `JudgementResult`s and refuses half anchors; rubric/panel
     `config/judging/qa-entailment-{rubric,panel}-v1.yaml` (unprovisioned like every panel) and
     the one correctness-dimension judge item in `judge_contracts` (Q&A entailment only). Missing
     facts zero, repeated facts no credit, empty answer recall zero with undefined claim
     precision, contradictions zero credit even under unanimous votes. The native presence
     aggregation is computed with the same formula and stored separately.
   - Citation validity, grounding and unsupported/contradicted-claim diagnostics with explicit
     unknown states (PCB-27-3): `packages/evaluation/src/polycodebench_evaluation/qa_grading.py`
     (`entailment_packet_input`, `QaVoteSet`, `grade_qa`) produces `QaMetrics` where grounding,
     claim precision and claim rates are separately named diagnostics; incomplete verification
     yields `None` plus named `incomplete_verifications`, never zero; `code_dimensions` is always
     `not_applicable` and no security/runtime/idiom score exists on the metrics.
   - Admitted cross-file fixture `taskpacks/qa/py-configkit-qa-v1` (PCB-27-4): a runnable
     three-module cfgkit snapshot with facts verified across `cfgkit/loader.py` and
     `cfgkit/interpolate.py`, versioned `hidden/oracle.json`, and answer variants
     (reference, paraphrased alternative, wrong-citation, contradiction, empty, repeated).
     Method records: `docs/implementation/qa-method.md` (DeepCodeBench-inspired boundary,
     native-versus-inspired table, explicit non-claims) and `docs/methodology/deepcodebench.md`.
   - Ledgers: `tickets.md` PCB-27-1..4, `e2e-matrix.md` E2E-38 (now passed across both families,
     merged with the concurrent session's prediction subcases), `requirements-matrix.md` WP-20,
     `decisions.md` D-27-01..04, `commands.md`, `progress.json`; evidence
     `docs/implementation/evidence/prompt-27-e2e-38.json`.

2. Tests/commands actually run and their results
   - `uv run pytest -q tests/test_qa_contracts.py`: PASS, 14 (pinned inputs; read/search-only
     protocol refusals; retrieval context/truncation logging; envelope parsing refusals for
     malformed/ambiguous/non-quoting submissions; citation binding to the base snapshot incl.
     foreign digests, absent paths and out-of-bounds spans; frozen claim extraction; deterministic
     contradictions; recall weighting/repetition/empty/contradiction; separate native aggregation;
     unknown-vs-zero rate semantics).
   - `uv run pytest -q tests/test_qa_grading.py`: PASS, 10 (E2E-38 Q&A cases through the real
     judge services - build_packet/parse_vote/aggregate with fixture votes: full recall over
     paraphrases, missing/repeated/empty semantics, contradiction zero-credit under unanimous
     votes, wrong citations flagged with grounding 0 while recall stays 100, incomplete
     verification unknown-not-zero, native aggregation preserved separately, six code dimensions
     N/A; adversarial fixtures: half-anchor votes refused, blinded fixed packet).
   - `uv run pytest -q tests/test_qa_fixtures.py`: PASS, 8 (pack import as `repo_qa`/`inspired`;
     oracle verifying spans match snapshot lines; reference and paraphrased alternative both
     100.000000; wrong-citation all-citations-flagged; contradiction credited zero and counted;
     empty recall zero with undefined precision; repeated equals reference; code dimensions
     N/A with no invented fields).
   - Regression `uv run pytest -q tests/test_qa_contracts.py tests/test_qa_grading.py
     tests/test_judging_core.py tests/test_judge_inputs.py tests/test_judge_cli.py
     tests/test_judging_postgres.py tests/test_solve_core.py tests/test_solve_sessions.py`
     (real PostgreSQL 17.6): PASS, 187 passed / 12 skipped (PCB_TEST_DOCKER-gated) — the
     `judge_contracts` entailment-dimension extension is regression-clean.
   - `uv run python scripts/export_contract_schemas.py --check`: PASS (21 generated files).
     `uv run ruff format` + `uv run ruff check` over changed paths: PASS. `uv run mypy` over
     qa_contracts/qa_prompts/qa_grading: no issues.
   - Fixture verification: oracle spans printed against the snapshot files; the pack's
     pass/fail matrix was re-run through the real contracts (one test-harness bug fixed:
     Windows path separators in the test snapshot helper, not in the pack).
   - Not run: live judge/model calls (panels unprovisioned), production-worker execution,
     prediction-family cases (Prompt 28 scope — recorded complete by a concurrent session in
     `evidence/prompt-28-e2e38.json`, not independently re-verified here), PCB_TEST_DOCKER
     agent tests.

3. Acceptance gates
   - Satisfied: PCB-27-1 (citations reference the base snapshot; editing disabled; retrieval
     context and truncation logged), PCB-27-2 (oracles/paraphrases/fixed entailment judging with
     preserved native aggregation; missing/repeated/empty semantics), PCB-27-3 (citation,
     grounding and unsupported/contradicted diagnostics with explicit unknown states; prose gets
     no invented code-dimension scores), PCB-27-4 (cross-file fixtures with verifying spans,
     alternative wording, wrong citations, contradictions; DeepCodeBench-inspired records; all
     fact evidence versioned).
   - E2E-38 Q&A variants: passed at the recorded tier (fixture judge votes through the real
     judge services; admitted authored `inspired` fixture; no live-model run claimed). The
     prediction variants' subcases are recorded by Prompt 28; the full E2E-38 row is merged and
     `passed` with both tiers disclosed. Prediction variants were retained as pending for this
     prompt's work and are owned by Prompt 28's evidence.
   - WP-20 now records all seven Track B families implemented. Pending: live-model runs and
     production tier everywhere (shared project blockers). Blocked: none introduced by this prompt.

4. Decisions or specification discrepancies recorded
   - D-27-01 (fact recall = native-style formula over mean-of-three entailment credits; native
     presence aggregation preserved separately), D-27-02 (grounding/claim diagnostics separately
     named; incomplete verification is unknown, never zero), D-27-03 (binary entailment votes;
     half anchors refused at the frozen conversion; correctness items limited to Q&A entailment),
   - D-27-04 (read/search-only protocol over a pinned snapshot; claims must quote the answer;
     retrieval log is a view of the existing tool records). No specification discrepancy found.

5. Exact next command or numbered prompt
   - Next: Prompt 29 — Implement the complete public API and projections. (Prompt 28's prediction
     suites are recorded complete by a concurrent session in `evidence/prompt-28-e2e38.json` and
     were not independently re-verified by this session.)
