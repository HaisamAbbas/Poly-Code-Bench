# Prompt 94 / BWP-12 — Optional behavioral diagnostics

## Implemented functionality and changed files

- Added strict behavioral method-registry, task-validity, schema-v2 preregistration, observation, and assessment documents in `packages/core/src/polycodebench_core/benchmark_audit_documents.py`. The plan binds original/control pairs, family-disjoint calibration/validation/held-out splits, independent semantic/difficulty review, target/reference contexts, private prompt/tool/decoding/grading artifacts, statistical test, alpha, effect, target power, bootstrap count, multiplicity, decision rule, exposure policy, and separate model/training caps.
- Registered ConStat against its published source and statistical boundary. Its executable implementation is explicitly `not_pinned`; no accuracy heuristic or prose-derived likelihood stands in for the method. ConStat compares primary/reference performance with difficulty correction; that comparison alone does not establish training inclusion. See the [SRI publication page](https://www.sri.inf.ethz.ch/publications/dekoninck2024constat), [NeurIPS 2024 paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/a7f89793b9e6f8c6568dbbb6ff727b9b-Paper-Conference.pdf), and [official repository](https://github.com/eth-sri/ConStat).
- Added applicability checks and descriptive reconciliation in `packages/services/src/polycodebench_services/behavioral_diagnostics.py`. Checks use adapter-verified capabilities and accepted validity evidence. Assessments require an explicit outcome for every planned task/model unit and retain negative results, failed/blocked/not-run units, costs, tokens, access-event counts, and independent-family power limits. The service does not dispatch model calls or calculate ConStat statistics.
- Extended immutable persistence checks in `packages/persistence/src/polycodebench_persistence/benchmark_audit.py`. They bind diagnostic calls to the pre-existing audit run, exact task, pinned model revision, decoding config, private request/response artifacts, ordinary gateway intent/deliveries, per-delivery sealed access events, and latest usage settlement. Denied attempts remain recorded without claiming a dispatch. Retries count against the frozen model-call cap.
- Added audit-document kinds and unique root/successor indexes for per-plan/pair/task-role/model observations in migration `a194d6c3e781`. Ambiguous gateway outcomes can resolve through a linear immutable successor; assessments must use the current head. The downgrade refuses to remove behavioral evidence. Updated the persistence metadata contract, canonical vectors, focused contract tests, and `tests/test_behavioral_diagnostics.py`.

## Tests/commands actually run and results

- Focused behavioral, document, and persistence-metadata checks: **31 passed**.
- Combined Prompt85–94 audit regression: **162 passed**. Tests use synthetic contract fixtures; no model/source call or database write occurred.
- Ruff check and format check: passed for all changed Python and migration files.
- Strict Mypy: passed for core behavioral documents, diagnostic service, persistence repository, and behavioral tests.
- `corepack pnpm --filter @polycodebench/contracts test:contracts`: passed; TypeScript canonical envelope and property checks passed.
- Alembic: `a194d6c3e781` is the only head. Offline upgrade from `93b11c2d7e4f` and guarded downgrade to it both rendered successfully. Neither ran against PostgreSQL.
- `uv lock --check --offline`: passed; 138 packages resolved.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-31 / BAT-12-A:** method registry and fail-closed applicability are implemented. ConStat remains unsupported until an exact reviewed executable artifact and matching approved model capabilities are pinned.
- **Partial — BX-32 / BAT-12-B:** frozen plans validate family-disjoint splits, reviewed controls, target/reference contexts, exact config artifacts, statistical settings, multiplicity, budgets, and a decision rule. No independent live control-review evidence exists.
- **Partial — BX-33 / BAT-12-C:** complete planned-unit and retry/accounting reconciliation is implemented. Persistence code and migration only received offline/static checks; no production gateway or audit database was available.
- **Partial — BREQ-13, BREQ-14, BREQ-26 / BAT-12-D:** owned training manifests require exposure-separated families and a separate compute cap. Training-compute accounting reports unavailable, and calibration remains blocked without authorized owned training evidence.
- **BWP-12 remains partial.** The method adapter, accepted semantic/difficulty reviews, approved model contexts/exposure writer, live PostgreSQL persistence, controlled training manifest, and calibrated power analysis are absent. No model call occurred.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-31:** ConStat is registered but not executable until its exact implementation is pinned. A generic high-accuracy detector is not labeled ConStat, and performance gaps never become training-inclusion claims.
- **ADDENDUM-DECISION-32:** every model retry needs its own sealed access event and gateway delivery record; response, usage, cost, and missingness reconcile to the ordinary model ledger. Requests and responses remain private. An ambiguous outcome can be resolved only by one immutable successor that preserves the planned unit, request digest, call intent, and prior access history.
- **ADDENDUM-DECISION-33:** family-level power is `insufficient_families` with fewer than two families in any frozen split; otherwise it remains `not_estimated` until a reviewed power-analysis adapter exists. Owned-training compute remains explicitly unavailable rather than zero.
- The earlier §1.1 source-list discrepancy remains recorded in the Prompt93 report: the specification says five source Markdown files, while it lists/hashes three.

## Exact next command or numbered prompt

Proceed to **Prompt 95 / BWP-13 — Firewall admission and independently validated replacements**. Preserve the original benchmark and require complete scoped evidence, rights, validity, lineage, and independent review before admission.
