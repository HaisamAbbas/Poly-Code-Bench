# Prompt 18 Track A gap map

The Prompt 17 report remains the source of truth for the blocked real pilot. The user explicitly
authorized Prompt 18 independently while deferring those pilot inputs; this implementation does
not satisfy or change the Prompt 17 gate.

| Obligation | Existing service reused | Prompt 18 implementation | Evidence / limit |
|---|---|---|---|
| Historical, disclosed, authored, injected and mutated defect provenance | Frozen task/source records; `PlanRunner` and pinned language plugins | Typed provenance/admission records, reproducible authored-history/injection/mutation builders, immutable source digests, clean-control identity, private oracle/log projection rules and mutation rejection conditions | Python and Rust authored internal examples reproduce before repair and after mutation, while references pass in local Docker. External disclosed-security examples remain blocked by the machine-readable source requirement. |
| Structured findings and causal one-to-one scoring | Shared `Finding` contract and canonical digest utilities | Strict base-bound parser, span validation, exact/semantic duplicate tracking, reviewer-only match proposals and deterministic maximum-weight matching | E2E-32 internal fixture records TP=1, FP=1, FN=1, one duplicate and P=R=F1=0.5; missed-bug L/X/V credit is zero. |
| Novel findings and oracle updates | Existing evidence references and immutable record digests | Explicit pending/novel/false-positive dispositions, append-only hash-chained review events, versioned ground truth and fail-closed cohort rematching | E2E-33 internal fixture rematches every declared evaluation against one oracle digest; missing a rematch is rejected. No human adjudication is claimed. |
| Combined-patch repair, attempt failures and language-balanced results | Existing `guest_helper.plan_patch`, fresh candidate identity, `Evaluator` and scoring composite | Allowlisted/protected patch application on a fresh base; independent evaluation; failed repair zero while detection remains; explicit model-failure zero credit, missing-attempt coverage loss, entry-isolated aggregation, and fixed source/language strata with equal Python/Rust headline weights | E2E-34 includes actual development-sandbox evaluation of an authored bad patch, plus unit coverage for clean-control false alarms and no repair bonus. No model submission is evaluated. |

Track A code and the E2E fixtures are not model benchmark results. Phase 3 cannot pass until the
accepted Prompt 17 pilot prerequisite is satisfied. Full public-source admission, human review
operations, production isolation and publication are outside the evidence obtained here.
