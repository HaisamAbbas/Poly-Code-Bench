# Phase BA2 — Evidence, retrieval and observed risk

## Implemented functionality and changed files

- Prompt89 added bounded deterministic candidate selection, coverage reconciliation, scoped cache identity and replay from stored hit tuples. See [prompt-89.md](reports/prompt-89.md).
- Prompt90 added v2 match evidence, content/span verification, relation labels, independent review/adjudication and correction-successor contracts. Source and rights trust remain unverified. See [prompt-90.md](reports/prompt-90.md).
- Prompt91 added versioned observed-risk policy/assessment documents, all eight signal descriptors, the pure M/C/E/L Decimal scorer, missingness bounds and constrained claim wording. The audit document schema now permits v1/v2 via a guarded reversible migration. See [prompt-91.md](reports/prompt-91.md).
- BA2 keeps retrieval, evidence review and scoring as distinct evidence stages. Candidate hits do not become accepted evidence; incomplete scope does not become a zero score; and a heuristic index is not a model training-membership probability.

## Tests/commands actually run and results

- Prompt89 focused and combined regression — **44** and **97** passing tests respectively; the combined Prompt85–89 suite was run twice. Results and the transient first-run Windows diagnostic are recorded in [commands.md](commands.md).
- Prompt90 focused and combined regression — **36** and **109** passing tests, plus strict Mypy/Ruff and TypeScript/Python canonical v1/v2 contract checks. No database, source, human or model call occurred.
- Prompt91 combined Prompt85–91 regression — **121 passed**. Strict Mypy and Ruff/format checks passed for changed schema, scorer, migration and test files. Offline migration upgrade and downgrade SQL both rendered; neither was executed against a live database.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-16–17 / BWP-07:** deterministic selection, explicit coverage and local replay behavior are tested. Approved source snapshots/indexes and durable fenced query/cache/checkpoint storage are absent.
- **Partial — BX-18–21 / BWP-08:** content and span integrity, relation definitions, dispute handling and correction successors are tested. A trusted source/rights resolver, independent live review records and persistent history are absent.
- **Partial — BX-22–24 / BWP-09:** exact score/bound goldens, fixed thresholds, signal separation and missingness gating are tested. Detector calibration, accepted live source evidence, database migration execution and API/report integration remain unavailable.
- No BA2 gate is complete based on synthetic fixtures. No source fetch, index query, model dispatch or PostgreSQL write was run.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-20–22:** selection is bounded and deterministic, query coverage/replay never infer success from exceptions, and cache identity includes permission scope.
- **ADDENDUM-DECISION-23–26:** v2 evidence preserves v1 history; content integrity is not trusted provenance; opinions/corrections append; source instructions and model outputs cannot change policy or verdict authority.
- **ADDENDUM-DECISION-27–30:** observed risk uses fixed weighted groups and no probability language; missing groups retain full upper-bound weight; signal evidence must be explicitly reviewed; and v2 storage requires a guarded schema migration.
- The repository has no approved source/corpus bytes or rights manifests, independent match/score calibration set, live reviewer service or audit database connection. Those missing inputs are recorded as blocked evidence, not replaced with fixtures.

## Exact next command or numbered prompt

Proceed in order to **Prompt92 / BWP-10**. Implement temporal holdout and model-context records with unknown cutoffs and chronology retained as uncertainty; request approved source/model context before making live claims.
