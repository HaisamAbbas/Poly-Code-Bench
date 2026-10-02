# D-15-01 - The scorer is a pure function, and purity is enforced, not promised

`score_evaluation(task, policy, evidence)` imports nothing that can execute code, call a provider,
reach a database or read a clock. The recorded timestamp and the scorer-source digest are **inputs**
carried on the manifest's `ScoringInvocation`, never values the scorer looks up. `scorecard_id` is
derived from the score's own content digest rather than from a sequence or a random source.

This is checked two ways rather than asserted in a comment: a static import scan over the pure
modules, and a replay in a fresh interpreter that installs a runtime import tripwire over provider
clients, network stacks and every higher layer.

**Consequence for callers:** a caller that wants a wall-clock `created_at` or a scorer digest must
supply them. That is deliberate: it is what makes score replay exact rather than approximate.

# D-15-02 - Exact weights in the sum, integer basis points in the presentation

Technical Spec 14.3 gives `CodeScore = 30 + 70 × Σ(w_d × q_d) / (100 × Σw_d)`, and the 14.6 worked
example is `30 + 70 × (47.75 / 55) = 90.772727`. That is an exact rational expression; no integer
allocation of 10000 basis points reproduces it. The composite is therefore computed from exact
`Fraction` weights, while the integer `effective_weight_bps` on the `ScoreItem` contract - which the
core schema fixes as an integer - is the rounded largest-remainder presentation of those exact
weights, allocated to sum to exactly 10000.

Both are recorded on every explanation row (`effective_weight_bp` exact and
`effective_weight_bps` integer), so the chain is reproducible either way and the exact one is the one
that sums to the published total. The same applies inside a dimension: leaves split the exact
dimension weight in proportion to their frozen item weights, and renormalise over the *applicable*
weights only, as 14.4 requires.

# D-15-03 - A failed gate zeroes correctness too

The composite is written `30g + g × (quality)` (Architecture 8.3), so a failed gate multiplies the
whole expression by zero. Every item, including the correctness item, therefore carries
`contribution = 0.000000` and `gating_reason = gated_by_correctness`. The `Scorecard` contract
requires exactly this for `gate = fail`. A predeclared N/A item stays N/A and keeps a zero weight.

# D-15-04 - Missing evidence blocks; it is never a zero and never a hundred

Required analyzers, unresolved rubric items, unadjudicated high-impact issues and an unknown gate
produce `status = needs_review` (or `infra_blocked` when the cause is infrastructure) with
`total_score = null`. The explanation names every blocking reference. This satisfies 14.6's
"Required analyzer missing on passing code | No publishable composite; not 100 or zero" without
inventing a neutral value.

An *applicability* disagreement is different and is refused rather than blocked: a manifest whose
dimension set disagrees with the frozen task plan is a producer defect, not an incomplete
evaluation.

# D-15-05 - The efficiency dimension value is an evaluator output, cross-checked against its ratios

Technical Spec 13.4 owns the efficiency transform, so WP-13 produces the dimension value and WP-15
consumes it. The manifest nevertheless carries the ratios, and when they are present the scorer
re-derives the value from the policy's own breakpoints and **refuses** a disagreement
(`efficiency_score_mismatch`). This keeps the §14.6 fixture "time ratio 2 and memory ratio 1.5 gives
61.666667" verifiable through the scorer, while leaving WP-13 the single owner of the measurement.

## Discrepancy: T 14.4 defines the idiomatic dimension by the language rubric; T 15.1 keeps two
## residual idiom judge items in it

**Affected clauses:** T 14.4 ("Idiomatic strength: weighted task-applicable items from the
orthogonal language rubrics in §18"), T 15.1/§15.2 (`config/judging/rubric-v1.yaml` declares
`idiomatic_design` and `error_handling_clarity` in the `idiomatic` dimension), A 8.7 ("a predeclared
subset of language-specific items whose primary score owner is idiomatic strength, with weights
renormalized within that subset").

**Evidence:** the language profile's idiom items sum to 10000 basis points on their own
(`config/languages/profiles-v1.yaml`), so there is no room left for the two residual judge items
that the judge rubric assigns to the same dimension. The scorer cannot compute the dimension's value
without a declared split.

**Chosen resolution (nonbreaking, narrowest compatible interpretation):** the scoring policy
declares the split as frozen pilot parameters - `language_rubric_weight_bp: 8000` with
`residual_judge_items: {idiomatic_design: 1200, error_handling_clarity: 800}`, and the same shape
for robustness (`scenario_weight_bp: 8000`, `residual_robustness_reasoning: 2000`). The language
rubric keeps its own relative weights and is renormalised into its block, exactly as A 8.7 says a
subset's weights are renormalized. The scorer *computes* the expected item weights from the policy
and refuses a manifest that declares anything else, so no producer can redefine the split.

**Alternative rejected:** folding the judge items into the nearest rubric item. That would let a
judge's judgement silently move a language-rubric weight, which is the double-counting A 8.7 exists
to prevent.

**Scope:** pilot parameter only. `calibration_status: pending`; both weights must be recalibrated
before any scored release. If the owner prefers a different split, it is a one-line policy change
that produces a new policy digest and therefore new scorecard identities - not a rescore.

# D-15-06 - Duplicate evidence moves the evidence identity, not the score

The `Scorecard` records `evidence_manifest_digest`, so retaining a duplicate report necessarily moves
the scorecard's digest. That is correct: the evidence genuinely changed. What must not change is the
score. `score_identity()` digests only the awarded numbers (gate, status, total, per-dimension
values/weights/contributions and per-item values/contributions), and the duplicate test asserts
that it is stable while the scorecard digest moves.

Correspondingly, the manifest's own `content_digest()` is **order-insensitive**: every repeated
collection in a manifest is a set, so two producers that emit the same evidence in a different order
produce the same digest and therefore identical canonical output.

# D-15-07 - `policy_digest` lives on the scorecard and on every explanation row, not on ScoreItem

Architecture 12.3 lists `policy_digest` among the fields each score item records. The frozen
`ScoreItem` contract from Prompt 02 has no such field, and adding one would change the published
public schema and every generated client. The narrowest compatible reading is used instead: the
scorecard carries `scoring_policy_digest` and every `ItemExplanation` row carries `policy_digest`, so
the requirement is met without duplicating a constant across every item and without amending a
frozen public contract.

# D-15-08 - Archives keep every field, including the non-semantic identity fields

`canonical_document_bytes` deliberately drops `run_id`, `candidate_id` and `scorecard_id`. Scoring
needs those as *inputs*, so an archive written in the canonical envelope could not be replayed. The
loader therefore archives `model_dump(mode="json")` - every field - and reads either that or a
canonical envelope. The digest recorded on the scorecard is the manifest's own content digest, which
does cover those fields, because for a manifest they are semantic: two different candidates are
different evidence.

# D-15-09 - Scoring may depend on the shared plugin contracts

`check_boundaries.py` now allows `scoring -> plugins_api`. Technical Spec 14.1 names `FrozenTask` as
a scorer input, and that type lives in `polycodebench_plugins_api`, so consuming it is the
specification's own contract rather than a new dependency. The scorer still may not depend on any
layer that can execute code, call a model or reach a database.

# D-15-10 - `py.typed` added to core and plugins-api

Without `py.typed`, `mypy --strict` could not see either package's types and every downstream module
had to suppress `import-untyped` - which also silently turned `ContractModel` into `Any` and made
subclassing it unchecked. Adding the markers removed the whole class of suppression and surfaced two
genuine typing defects in `judge_contracts.py`/`judge_calibration.py`, which were fixed without
behaviour change. Only one pre-existing lint finding remains in that in-flight Prompt 14 file.