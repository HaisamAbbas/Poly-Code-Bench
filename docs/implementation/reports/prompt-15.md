Prompt 15 / Phase 2 — DONE

1. Implemented functionality and changed files

   Pure scorer implemented as `score_evaluation(task, policy, evidence)`: no model call, no
   network, no filesystem mutation, no database read, no task execution and no clock read. The
   recorded timestamp and the scorer-source digest are inputs on the manifest, and `scorecard_id`
   is derived from the score's own content, so replay needs no sequence or random source.

   - `packages/scoring/src/polycodebench_scoring/scorer.py` — the composite, gating, contribution
     chain, `ScoringOutcome`, `score_identity()`.
   - `.../arithmetic.py` — exact `Fraction` effective weights, largest-remainder integer
     presentation, the §13.4 piecewise efficiency transform, fixed-point six-place half-even output.
   - `.../manifest.py` — `ValidatedEvidenceManifest` and its parts: gate verdict,
     required-analyzer completeness records, canonical issues, weighted rubric items, the
     efficiency measurement, diagnostic views. Order-insensitive content digest.
   - `.../ownership.py` — family→owner resolution, duplicate-key collapse, refusal of
     contradictory owners and unjustified distinct consequences, `OwnershipLedger`.
   - `.../policy.py` — `FrozenScoringPolicy`: composite weights, severity penalties, efficiency
     breakpoints/weights, code-quality item plan and anchors, the idiomatic/robustness block
     splits, judge policy, explicit rounding mode.
   - `.../explain.py` — `ScorecardExplanation`: per dimension and per item, the nominal weight, the
     exact effective weight, the integer presentation weight, the arithmetic string, the evidence
     references and the policy digest.
   - `.../replay.py` — `replay_scorecard`/`replay_outcome` with score-schema and policy-digest
     version handling.
   - `.../loader.py`, `.../cli.py`, `.../contracts.py`, `.../errors.py` — file boundary, the
     `pcb-score score|replay` CLI, the strict base model and the refusal codes.
   - `config/scoring/pilot-v1.yaml`, `config/scoring/evidence_ownership.yaml` — the frozen policy
     and ownership configuration.
   - `scripts/hash_scoring_profile_source.py` — recomputes/verifies the digest that pins
     `config/languages/profiles-v1.yaml` into the scoring policy.
   - `scripts/check_boundaries.py` — `scoring -> plugins_api` declared.
   - `packages/core/src/polycodebench_core/{py.typed,judge_contracts.py,judge_calibration.py}`,
     `packages/plugins-api/src/polycodebench_plugins_api/py.typed`.
   - Tests: `tests/scoring_support.py`, `tests/test_scoring_golden.py`,
     `tests/test_scoring_properties.py`, `tests/test_scoring_policy.py`,
     `tests/test_scoring_replay.py`.

2. Tests/commands actually run and their results

   - `.venv/Scripts/python.exe -m pytest tests/test_scoring_golden.py tests/test_scoring_properties.py tests/test_scoring_policy.py tests/test_scoring_replay.py -q -p no:cacheprovider` — PASS, 66 passed in 13.86s (local fixture tier; no network, no provider, no task execution).
   - `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider` with the in-flight Prompt 13/14/16 modules and the three modules that fail on this tree without Prompt 15 ignored — PASS, 488 passed, 147 skipped in 78.52s.
   - `.venv/Scripts/python.exe -m mypy --strict packages/core/src packages/plugins-api/src packages/scoring/src packages/configuration/src` — PASS, no issues in 41 source files.
   - `.venv/Scripts/ruff.exe check` and `ruff format --check` on the Prompt 15 paths — PASS (18 files already formatted).
   - `.venv/Scripts/python.exe scripts/check_boundaries.py` — PASS.
   - `.venv/Scripts/python.exe scripts/hash_scoring_profile_source.py` — PASS, the policy records the real profile-source digest.
   - `.venv/Scripts/python.exe docs/implementation/verify_prompt00.py` — PASS, 14 REQ / 24 WP / 43 E2E / 142 PCB tickets.
   - `uv lock --offline`; `uv sync --locked --offline --all-packages` — PASS.
   - `uv run --locked --offline --package polycodebench-scoring pcb-score score ... --explain` — PASS, printed the full contribution chain, `total: 84.000000`.
   - `uv run --locked --offline --package polycodebench-scoring pcb-score replay ... --archived-outcome ...` — PASS, `{"outcome_digest": "sha256:4f8b9eab...d32133", "replayed": true}`.
   - `uv build --all-packages --offline` — PASS.

   Not run, with reasons: production worker tier, sealed hidden lane, live model/judge endpoints
   and human calibration are unconfigured and unauthorized on this host, so the policy stays
   `effective_for_scoring: false` / `calibration_status: pending`. A repository-wide
   `ruff check .` still reports 71 findings in the in-flight Prompt 14/16 modules and `tmp_probe/`,
   plus one pre-existing `UP012` in `judge_calibration.py`; these are outside Prompt 15's scope
   and were left for their owners.

3. Acceptance gates

   Satisfied:
   - PCB-15-1 — `tests/test_scoring_golden.py`, `tests/test_scoring_properties.py`. A failed gate
     zeroes every item including correctness (the composite is `30g + g × quality`); missing
     required evidence, an unresolved item, a missing analyzer or an unadjudicated high-impact
     claim yields `status=needs_review` with `total_score=null` — never `0.000000`, never `100`.
   - PCB-15-2 — `tests/test_scoring_golden.py`, `tests/test_scoring_policy.py`. The composite sums
     exact rational weights while the integer `effective_weight_bps` on the frozen `ScoreItem`
     contract is the rounded presentation; both are recorded with the arithmetic and the policy
     digest. Diagnostic profile items carry `composite_weight_bp: Literal[0]`, so they cannot
     contribute by construction, and an issue owned by another dimension never lowers security.
   - PCB-15-3 — `tests/test_scoring_properties.py`, `tests/test_scoring_replay.py`. Duplicate issue
     keys collapse (security 75.000000, once), reordering equivalent evidence gives a byte-identical
     outcome, `score_identity()` is stable under duplication while the evidence digest correctly
     moves, and duplicate/unjustified ownership is refused.
   - PCB-15-4 — `tests/test_scoring_replay.py`, plus `pcb-score replay` through `uv run`. A fresh
     interpreter reproduced the archived canonical scorecard with a runtime import tripwire over
     provider clients, network stacks and higher layers, and an in-process clock/environment/
     filesystem guard. A changed policy digest and an unsupported score-schema version are refused.
   - E2E-23 — passed (fixture). All seven §14.6 results plus the §24.1 properties.
   - §14.6 synthetic fixtures, exactly: `89.750000`, `90.772727`, `75.000000`, `61.666667`,
     `0.000000`, plus `100.000000` and "no publishable composite". No formula was changed to make a
     current implementation output pass.
   - Boundedness, monotonicity at fixed applicability, duplicate/reordering invariance,
     unknown-evidence non-increase and N/A preservation — all exercised in
     `tests/test_scoring_properties.py`.

   Pending (not blocked on code):
   - E2E-24 live-pilot replay and restored-environment replay. The clean-process archived-fixture
     variant passed; the live variants need Prompt 17's actual run and Prompt 33's restore drill.
   - The idiomatic/robustness residual-judge weight split (8000/2000) is a declared pilot
     parameter awaiting calibration; the scorer enforces it, so recalibration is a policy change
     with a new digest, never a rescore.

   Blocked (external):
   - Human/judge calibration labels (Prompt 14) and authorized provider/worker targets (Prompt 17).
     The scorer consumes `ItemOutcome`-shaped evidence and never calls a judge, so this is a
     dependency, not a defect.

4. Decisions or specification discrepancies recorded

   - D-15-01 purity enforced, not promised (static import scan + runtime tripwire + clock guard).
   - D-15-02 exact rational weights in the sum, integer basis points in the presentation; the
     §14.6 N/A example is only reproducible from the exact form.
   - D-15-03 a failed gate zeroes correctness too.
   - D-15-04 missing evidence blocks; an applicability disagreement is refused.
   - D-15-05 the efficiency dimension value is WP-13's output, re-derived from its ratios by the
     scorer and refused on disagreement.
   - **Discrepancy (T §14.4 vs T §15.1):** the idiomatic dimension is defined by the language
     rubric (10000 bp) yet the judge rubric assigns two residual idiom items to the same dimension,
     leaving no room. Proposed and implemented resolution: a frozen policy split
     (`language_rubric_weight_bp: 8000`, residual judge items 1200/800; robustness 8000/2000), with
     the language rubric renormalised within its block exactly as A §8.7 describes, and the scorer
     computing the expected weights so no producer can redefine the split. Alternative rejected:
     folding judge items into a rubric item, which would let a judge silently move a language weight.
     Pilot parameter; recalibration pending.
   - D-15-06 a duplicate report moves the evidence identity, not the score.
   - D-15-07 `policy_digest` on the scorecard and every explanation row rather than added to the
     frozen public `ScoreItem` schema.
   - D-15-08 archives keep every field, because scoring needs `run_id`/`candidate_id` as inputs
     that canonical bytes deliberately drop.
   - D-15-09 `scoring -> plugins_api` boundary allowed, per T §14.1's `FrozenTask` input.
   - D-15-10 `py.typed` added to core and plugins-api, which removed the `import-untyped`
     suppression class and surfaced two real typing defects (fixed without behaviour change).

5. Exact next command or numbered prompt

   Next: Prompt 16 — Implement aggregation and release publication.